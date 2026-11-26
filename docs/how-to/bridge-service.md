---
status: current
last_reviewed: 2026-11-20
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
| `JARVIS_PROJECTS` | yes | Comma-separated `name=/absolute/path` pairs, e.g. `demo=/home/you/code/demo`. The phone selects a project by name in the request. Or `*`: every git checkout directly under `JARVIS_PROJECTS_ROOT` is a project, named after its directory (see "Every checkout under a root" below). |
| `JARVIS_PROJECTS_ROOT` | with `*` only | The absolute directory that holds the checkouts, e.g. `/work`. Must exist at start. Refused when `JARVIS_PROJECTS` is a list. |
| `JARVIS_PORT` | no (default `8787`) | TCP port on the Tailscale interface. |
| `JARVIS_STATE_DIR` | no (default `~/.jarvis`) | Where the per-project session id, the log file and the drive journal (`journal/`) are stored. Outside the repo, never in git. |
| `JARVIS_DRIVE_GAP_MINUTES` | no (default `30`) | Minutes without an event after which the next one starts a new drive (see "The drive journal"). 1 to 1440. |
| `JARVIS_JOURNAL_DAYS` | no (default `30`) | Days a drive is kept in the journal after its last event. 1 to 366. |
| `JARVIS_RECAP_WORDS` | no (default `where was i,what was i doing,waar was ik,wat was ik aan het doen`) | The whole utterances that ask for the recap, comma-separated, written as they are compared: lower case, no punctuation, single spaces. |
| `JARVIS_NOTE_WORDS` | no (default `note,make a note,take a note,noteer,notitie,maak een notitie`) | The first words that make an utterance a note, in the same form. None of these, nor a recap word, may also be a yes or a no of `full` mode (refused at start). |
| `JARVIS_WHISPER_MODEL` | no (default `base`) | faster-whisper model size; CPU-friendly sizes are `tiny`, `base`, `small`. |
| `JARVIS_WHISPER_LANGUAGE` | no (default: detected per recording) | A language code (`en`, `nl`) to skip Whisper's language detection, which costs a second pass over the recording: on `agent-lxc` (base, int8) 0.8 s of the 1.7-1.9 s a short question takes. Set it only when you speak one language to Jarvis: Dutch speech with `en` comes out as a rough English translation. See "Where a turn's time goes". |
| `JARVIS_WHISPER_LANGUAGES` | no (default: any language) | The languages you speak to Jarvis, comma-separated, at least two (`nl,en`). Whisper still detects the language per recording, but picks the likeliest of these only, then transcribes in it: the same one detection pass, so no slower. Short English in the car was detected as German on 26 Nov 2026 ("boots and cats" came back as "Okay, du buchst im Katz."); with `nl,en` it comes out as English or Dutch. Not together with `JARVIS_WHISPER_LANGUAGE`; one code is refused (set that one instead). |
| `JARVIS_WHISPER_BEAM_SIZE` | no (default `5`) | Whisper's beam width, 1 to 10. `1` (greedy) took another 0.05-0.2 s off with the same transcripts on the test recordings. |
| `JARVIS_KOKORO_VOICE` | no (default `bm_george`) | A Kokoro voice name. The default is a British male voice; the image also carries `af_heart` (American female). Pick one by ear once the service runs (see below). |
| `JARVIS_STREAM_REPLIES` | no (default on) | `0`: every client gets the whole WAV, also one that asks for a streamed reply. Unset or `1`: a client that asks gets the reply as it is rendered (see "Streamed replies"). Anything else is refused at start. |
| `JARVIS_SING` | no (default on) | `0`: Jarvis never sings; a reply meant to be sung is spoken as words. Unset or `1`: the easter egg is on (see "Singing (easter egg)"). Anything else is refused at start. |
| `JARVIS_SING_PARODY` | no (default on) | `0`: the parody tunes (`iron-man`) are taken out of the table on their own; a reply tagged with one is spoken as words, the public-domain tunes are still sung. Unset or `1`: they are in. Anything else is refused at start. `JARVIS_SING=0` stops all singing either way. The prompts still name `iron-man` when it is `0`, so Jarvis may offer it and then speak those lines. |
| `JARVIS_BEATBOX` | no (default on) | `0`: Jarvis never beatboxes; for a reply meant to be a beat he says "Beatboxing is switched off." Unset or `1`: the second easter egg is on (see "Beatboxing (easter egg)"). Independent of `JARVIS_SING`. Anything else is refused at start. |
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
[`jarvis-general-conversation`](../../openspec/changes/archive/2026-11-23-jarvis-general-conversation/design.md),
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
| notice `new_session` (a stored session id was discarded, or the project's last turn was more than an hour ago: [How long a session lives](agent-host.md#how-long-a-session-lives)) | "I started a new session for this project." |
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
| 200 | `audio/wav` with the spoken summary, or a streamed reply for a client that asks for one (see "Streamed replies" below). Headers: `X-Jarvis-Summary` (URL-encoded text of what is spoken), `X-Jarvis-Session-Id` and `X-Jarvis-Timing` (milliseconds per stage, see "Where a turn's time goes"). Silence gets a spoken "didn't catch that" and no agent turn. "Where was I" and "note ..." are answered by the bridge with no agent turn and an empty `X-Jarvis-Session-Id` (see "The drive journal"). In `full` mode also a read-back (then with `X-Jarvis-Needs-Confirmation: 1`) or a spoken error of the agent host, and `X-Jarvis-Projects-Changed: 1` when a new project became ready; see "Agent modes". |
| 400 | No project, an unknown project (in `full` mode: a name the agent host would not accept by its shape), an empty body, or a `Content-Length` that is not a number (digits only: `+5`, `5_0` or padded values are refused) |
| 401 | Missing or wrong token |
| 409 | That project is still busy with another turn (one turn per session at a time). The lock lives only while that turn runs, so names asked for in `full` mode leave nothing behind. The recording is transcribed first: "where was I" and "note ..." are still answered for a busy project. |
| 413 | Audio larger than 10 MB |
| 502 | The Claude Code turn failed or timed out (details in the log) |
| 408 | The request line and headers did not arrive within 10 seconds in total (any route, before the token is looked at). The connection is closed. |
| 503 | Eight connections are already open (any route, before the token is looked at). `Retry-After: 5`. |

Every reply closes its connection: the bridge answers HTTP/1.0 and keeps none alive (a
streamed reply's status line says HTTP/1.1, which a chunked body needs, and it closes too).

### Streamed replies

Change [`jarvis-streaming-speech`](../../openspec/changes/archive/2026-11-23-jarvis-streaming-speech/design.md)
(S1 to S8). A client that sends `Accept: audio/L16;rate=24000;channels=1` over HTTP/1.1 (the
phone app does since 2026-11-17) gets:

```
HTTP/1.1 200 OK
Content-Type: audio/L16;rate=24000;channels=1
Transfer-Encoding: chunked
Cache-Control: no-store
Connection: close
X-Jarvis-Session-Id: …
X-Jarvis-Summary: …
X-Jarvis-Timing: read=4;stt=1702;vad=61;agent=7650;total=9430
```

as soon as the agent has answered, with every `X-Jarvis-*` header a WAV reply would have
(`X-Jarvis-Needs-Confirmation`, `X-Jarvis-Projects-Changed` too), and then the audio:
16-bit linear PCM, mono, 24 kHz, **big-endian** (that is what `audio/L16` means, RFC 2586),
no header, one chunk per sentence. Kokoro renders the next sentence while the phone plays the
first. A sentence ends at `.`, `!`, `?` or `…` followed by white space and a word that does
not start in lower case, or at a line break; a sentence shorter than 30 characters is
rendered together with the next one ("Done." alone would leave a gap of two seconds while
the next sentence renders). The words and their order are never changed
(`bridge/tts.py`, `pieces`).

- **Who gets the WAV instead:** a request without that `Accept` value (`*/*`, `audio/wav`,
  `audio/L16` without `rate=24000`, `channels=2`, `q=0`), an HTTP/1.0 request, and everyone
  while `JARVIS_STREAM_REPLIES=0`. The WAV reply is exactly as before: HTTP Shortcuts and
  older apps see no difference. A client tells the two apart by `Content-Type`.
- **A read-back** (`X-Jarvis-Needs-Confirmation: 1`) is rendered whole, with the same Kokoro
  call as the WAV reply, before the status line goes out, and only then sent in chunks. It is
  never split or reordered, and a rendering error is still a 500 with nothing pending.
- **An error after the headers** (Kokoro fails, or the phone stops reading): the bridge logs
  it, writes no terminating zero-length chunk and closes the connection. A client sees a cut
  chunked body (`IncompleteRead` in Python, an `IOException` on Android), never a complete
  one. In `full` mode a read-back whose stream broke off on the bridge's side is cancelled
  on the agent host, as one that could not be written before. That rarely happens: a
  read-back fits in the socket buffers, so the bridge has usually written it, and kept it
  pending, before the phone plays it. The phone cancels it itself (below).

### Cancelling a read-back the phone did not play to its end

A read-back reply (WAV or stream) carries `X-Jarvis-Confirmation-Id`, the id the agent host
gave the pending call. When the phone did not play it to its end (a tap, a cut stream, a
playback error, the screen closed), it sends:

```
POST /turn/cancel?project=<name>&id=<confirmation id>
Authorization: Bearer <token>
```

The bridge answers `no` to the agent host for that call, as for a read-back it could not
write, and replies `200` with `{"cancelled": true}`. An id that is not pending (already
answered or cancelled, expired, another read-back, or `readonly` mode) changes nothing:
`{"cancelled": false}`, also on a second call. A cancel that comes while the bridge is still
writing the read-back is remembered for 60 seconds and applied when the write ends. No body
is expected, and none (with or without `Content-Length: 0`) is fine; a body over 10 MB gets
413. 401 without the token, 400 for a missing project or an id that is not 1 to 64 letters,
digits, `_` or `-`. A cancel names its project: an id sent for another project cancels
nothing. The route takes no project lock. If the phone cannot send it (killed, offline),
the call expires on the agent host after its `expires_s`, at most 60 seconds.
- `X-Jarvis-Timing` on a streamed reply has no `tts` (nothing is rendered yet when it goes
  out, except for a read-back); the log line has it.

Try it with curl (`--raw` shows the chunks; the PCM goes to a file):

```sh
curl -sS --http1.1 -D - -o reply.pcm \
  -H "Authorization: Bearer $JARVIS_TOKEN" -H 'Accept: audio/L16;rate=24000;channels=1' \
  --data-binary @question.wav "http://<bridge>:8787/turn?project=demo"
play -t raw -r 24000 -e signed -b 16 -B -c 1 reply.pcm     # sox
```

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

Only the summary is spoken, at most 160 characters of it (240 for a sung line, see
"Singing (easter egg)"; Claude Code is asked for
one sentence, two at most, of about 100 to 150 characters, because speech is the slow
part of a turn; a longer one is cut at its last sentence end within 160 characters,
else at its last semicolon, else at a word, with "…"), and only as it is when it contains nothing that looks like code or a path (backticks, slashes, file
names, shell operators) or like a credential (`sk-ant-`, or any run of 32 or more
letters, digits, `_` or `-`). A semicolon is speech: until 26 Nov 2026 it counted as
code, and 29 of 165 recorded turns lost a good summary to it (a command joined by one is
still caught, by its flags or calls).

When the summary has code in it, or there is no summary line, Jarvis says what can be said
(`summary.speakable`): first the summary line made speakable, else the reply's own first
sentences, at most 160 characters (only the first 4000 characters are read). Code blocks,
fenced or indented, tables and horizontal rules are left out;
list, quote and heading markup and markdown are taken off; a link is said as its text, a
URL as "the link", a path or file as its name ("bridge/server.py" as "server", "owner/repo"
as "owner repo"); inline code stays when it is one plain word. A path or inline code shaped like a credential
(also split by `/`: "wJalr.../PxRfi..."), or with a user or port in it, a URL of any scheme
(`postgres://user:pass@...` is "the link"), an scp remote (`git@host:repo`), and a sentence
that reads as code (a call such as `print(i)`, an assignment, a flag such as `--force` or
`-rf`, a leading `def`/`import`/`return`), are left out with their sentence, and so is
any word shaped like a credential or id (16 or more letters and digits mixed). These code
checks apply to the summary line too. Only when nothing is
left does the phone hear "Done, but I couldn't put the answer into words. Ask me to say it
again." It never mentions a log: Mark, 26 Nov 2026, "and he says check the log for
details, but cant show them".

In `full` mode the bridge gets only one line of the reply (`spoken`, jarvis-full-agent
D33). The agent host now sends a summary line with exactly what the bridge would make of
the whole reply (`summary.spoken_line`): the agent's own summary when it is speakable as it
is, else the speakable excerpt, at most 160 characters, else `""`. So only filtered text
leaves the agent host, and both modes say the same. The bridge speaks only the summary line
of the `spoken` field, never lines around it.
That part of the change needs the agent host reinstalled (`agent_host/install.sh`).

## Where a turn's time goes

Every answered turn writes one INFO line, in the file and on stdout, with numbers only
(no transcript, no summary):

```
project=jarvis-home timing read_ms=4 stt_ms=1702 vad_ms=61 agent_ms=7650 tts_ms=5420 write_ms=3 first_audio_ms=14837 total_ms=14840 reply=wav audio_in_s=5.5 audio_out_s=9.9 spoken_chars=143
```

| Field | What |
|---|---|
| `read_ms` | Reading the upload after the head arrived |
| `stt_ms` | Decoding the recording and Whisper's transcript |
| `vad_ms` | `full` mode only: the detected-speech measurement (D36, step 5) |
| `agent_ms` | The agent: in `full` mode the ssh round trip to the agent host (the turn record there splits it further, see [the agent host's records](agent-host.md#reading-the-records)); in `readonly` mode the local Claude Code turn |
| `tts_ms` | Kokoro rendering the spoken line (for a streamed reply: all its sentences together) |
| `write_ms` | Writing the WAV to the phone (for a streamed reply: all its chunks together; rendering and writing take turns) |
| `first_audio_ms` | From the request's head to the first audio byte written: for a WAV the start of the whole file, for a streamed reply the first sentence. The number to compare between the two |
| `total_ms` | From the request's head to the last byte written |
| `reply` | `wav`, `stream`, or `stream_cut` when a streamed reply broke off (see "Streamed replies") |
| `audio_in_s`, `audio_out_s` | Seconds of the recording and of the reply, from their WAV headers, or for a streamed reply from the bytes sent (`audio_in_s` is absent when the upload is not WAV) |
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
**Speech cannot start sooner** without streaming (built since, see "Streamed replies"): the agent host answers once, at the end
of the turn, and the WAV goes out with a `Content-Length` after it is complete. Streaming
is the next step: Kokoro yields audio per sentence, and the first sentence of a summary
(about 70 characters) is ready after about 2.5 s instead of 5.5 s; the rest renders faster
than it plays. That needs a streamed response (a WAV header without a length, or raw PCM)
and a player on the phone that starts on the first bytes (`AudioTrack` instead of the
in-memory `MediaPlayer`): about half a day on the bridge and a day in the app, for about 3
seconds off the time to the first word of every turn.

### Streaming measured (2026-11-17)

Kokoro run once on `agent-lxc` (4 vCPUs, the pod's size), voice `bm_george`, after a warm-up,
with summaries of the usual length. "WAV" is the whole line rendered, the moment the old
reply could start to go out; "first piece" is when the streamed reply's first chunk is ready.

| Summary | Characters | WAV | First piece | All pieces | Speech |
|---|---|---|---|---|---|
| "I can't search the web from here. I can read the repository …" | 115 | 4.22 s | **1.68 s** | 4.64 s | 8.8 s |
| "The pull request is green and has one approving review. Two comments …" | 116 | 5.43 s | **3.15 s** | 6.19 s | 9.1 s |
| "Done. The branch is pushed and the build started; it takes about four minutes. …" | 117 | 5.74 s | 1.04 s ("Done." alone) | 7.42 s | 9.6 s |

- The first word comes 2.3 to 2.5 s sooner on the bridge, plus the download of a WAV of about
  400 KB that the phone no longer waits for.
- Rendering in pieces costs 0.4 to 1.7 s more in all, but that time falls while the phone is
  already speaking: each later piece was ready before the one before it had been spoken, except
  after "Done.". There the next sentence took about 3 s to render after 0.6 s of speech, a gap
  of over two seconds. So a sentence shorter than 30 characters now goes with the next one; for
  the third summary the first piece is then "Done. The branch is pushed …" (78 characters), by
  the rate above about 3.3 s, still about 2.4 s before the WAV (estimated, not run again).
- On the phone the time to the first word is `first_audio_ms` in the log line plus the
  tailnet and the 300 ms pre-roll; it is measured on the first turns with the new APK (task 3.3
  of the change).

## The drive journal

Change [`add-drive-mode`](../../openspec/changes/add-drive-mode/design.md), phase 1 (D10,
D12, D18, D19). Every use by voice is part of a *drive*. A drive starts with the first event
after 30 minutes without one (`JARVIS_DRIVE_GAP_MINUTES`) and ends when the next one starts;
nothing is switched on.

**What is kept.** `$JARVIS_STATE_DIR/journal/<drive id>.jsonl`, one file per drive (the id is
the start time in UTC, `20261120T071500Z`), one JSON line per event:

| `kind` | Fields |
|---|---|
| `turn` | `t` (Unix time), `project`, `transcript`, `spoken` (what was said back), `turn_id` (the agent host's, `full` mode), `session_id` (Claude Code's, `readonly` mode), `read_back`, `answer` (the utterance was the yes or no to a read-back) |
| `note` | `t`, `project`, `text` (as transcribed, at most 1000 characters) |
| `recap` | `t`, `project` |

The directory is `0700`, the files `0600`. Each line is synced to disk before the reply goes
out, so a restart keeps everything up to the last completed event and the open drive goes on.
A line cut off by a crash (also in the middle of a character) is skipped, and the next event
starts on a line of its own. Transcripts, replies and notes go through the same redaction as
the bridge log before they are written (the bridge token, `CLAUDE_CODE_OAUTH_TOKEN`, `sk-ant-`
keys). A journal directory that cannot be read at start does not stop the bridge: it logs an
error, and "where was I" and notes are off (those utterances go to the agent) until it can. A drive is deleted 30 days after its last event
(`JARVIS_JOURNAL_DAYS`), checked at start and whenever a new drive starts; the recap never
uses a drive past that age, pruned or not. A turn is written
before it is spoken; a journal that cannot be written is logged and never costs the turn.
Full written replies are not copied: in `full` mode they are in the agent host's turn record
of that `turn_id`.

**The drive's own words.** Matched by the bridge on the driver's own transcript, after
transcription and before the agent; the agent's text is never matched. They cost no agent
turn, work when the agent host is down, and work for a project that is busy with a turn
(that request is transcribed instead of refused with 409).

| Said | Matched as | Jarvis |
|---|---|---|
| "Where was I?", "What was I doing?", "Waar was ik?", "Wat was ik aan het doen?" (`JARVIS_RECAP_WORDS`) | the whole utterance, ignoring case and punctuation: "where was I with the config" goes to the agent | The recap, at most three sentences, from the journal only (of the question and of the reply only the first sentence, at most 25 and 40 words): "Last, on wordsworth, you asked: why does the index test fail? I said: The fixture is stale." then, if any, "A turn on jarvis is still running." In a drive with no turn yet it recaps the last drive ("Last drive, on ..."). After a read-back and a yes it names the question, not the yes. With an empty journal: "There is nothing to recap yet." |
| "Note: check the token rotation tonight", "make a note ...", "take a note ...", "Noteer ...", "Notitie: ...", "Maak een notitie ..." (`JARVIS_NOTE_WORDS`) | the first words of the utterance; the rest is the note. An utterance that ends in a question mark is a question and goes to the agent ("notitie over de release, wat staat erin?") | "Noted." The note goes into the journal only, never to the agent. "Note." alone: "What should I note? Say note, then the note." |

A control word is the next utterance of its project, so in `full` mode it uses up a pending
read-back like anything that is not a yes: the agent host gets a `no`, and Jarvis adds "What
I read back was not done." A yes after it releases nothing.

Not yet (later phases of the change): "what's new", "cancel", "switch to ...", "go on";
arrivals; the drive log handed to the agent host at the end of a drive (phase 4). Until then
the notes wait in the journal on the bridge's volume.

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

## Singing (easter egg)

Change [`jarvis-sing`](../../openspec/changes/archive/2026-11-23-jarvis-sing/proposal.md) (Mark,
2026-11-17: "make jarvis able to sing"). Ask "Jarvis, sing a song", "zing iets" or
"sing happy birthday" and Jarvis sings a short tune in its own voice instead of speaking.

**How a reply becomes a song.** The bridge owns the melodies, in a small table in
`bridge/singing.py`. The agent picks one and writes only the words: it starts its spoken
line with a tag and its own lyrics, one syllable per note, syllables joined by hyphens
(`agent_host/prompt.md` in `full` mode, the appended prompt in `bridge/summary.py` in
`readonly` mode):

```
SPOKEN SUMMARY: [sing: twinkle] Lit-tle build, you're green at last, all the tests have fi-nal-ly passed
```

| Tag | Tune | Syllables (per line) | Tempo, length |
|---|---|---|---|
| `[sing: happy-birthday]` | Good Morning to All (Hill sisters, 1893) | 25 (6, 6, 7, 6) | 110 bpm, about 14.5 s |
| `[sing: twinkle]` | Ah! vous dirai-je, maman (18th century), the first two lines | 14 (7, 7) | 100 bpm, about 10 s |
| `[sing: frere-jacques]` | Frère Jacques / Vader Jacob (18th century) | 32 (4, 4, 3, 3, 6, 6, 3, 3) | 120 bpm, about 16 s |
| `[sing: mountain-king]` | In the Hall of the Mountain King (Grieg, Peer Gynt, 1875), the opening theme; also `mountain king`, `hall-of-the-mountain-king` | 26 (7, 6, 8, 5) | 100 bpm, the last two bars a quarter faster, about 8.6 s |
| `[sing: ode-to-joy]` | Ode to Joy (Beethoven, Ninth Symphony, 1824), the first two phrases | 30 (8, 7, 8, 7) | 120 bpm, about 16 s |
| `[sing: iron-man]` | **Parody**: riff after Black Sabbath, Iron Man (1970), the main riff once; also `iron man`, `black sabbath` | 15 (5, 6, 4) | 80 bpm, about 9 s |

A tune name is normalised before it is looked up (lower case, spaces and underscores to
`-`), so `[sing: Mountain King]` works too. The tempo is one per tune; a line that should
go faster (the Mountain King builds, as the piece does) is written with shorter notes.
The Mountain King is in B minor as in the score, with only the dominant (F sharp) held in
the organ; the Iron Man riff has a power chord (root and fifth) under every note.

The bridge then renders it, with the Kokoro it already has and numpy, nothing else:

1. Every word is rendered once by Kokoro, its silence trimmed, and cut into its syllables
   at the quietest point near each equal division (hyphens give the count; a word without
   them is counted by its vowel groups).
2. The key follows the voice: the tune's middle note is put two semitones above the voice's
   median speaking pitch, measured on those words (`bm_george` about 146 Hz, so happy
   birthday is sung in E flat, from B flat 2 to B flat 3).
3. Each syllable is moved onto its note with a pitch-synchronous overlap-add (TD-PSOLA):
   grains of two pitch periods, cut at the voice's own pitch marks, are laid down at the
   note's period, so the pitch is the note's and held steady, with a light vibrato
   (5.5 Hz, 0.3 semitone) after 0.18 s. The vowel is stretched to the note's length; the
   consonants keep theirs. More syllables than notes share a note; fewer hold several.
4. A soft organ (the chord tones an octave below the tonic, the root two below, four
   harmonics) goes under the voice at about a third of its loudness; the song is
   normalised and faded out. At most 20 s: a tune that would take longer is sung faster.

A sung reply is rendered whole before the status line, in both forms: a WAV, or the same
L16 stream as a spoken reply, in pieces of half a second. `X-Jarvis-Summary` carries the
lyrics as words. Rendering took 4 to 9 s on `agent-lxc` (2026-11-17, Kokoro warm: about
0.6 s per distinct word), so a song starts later than a spoken reply of the same length.
When rendering fails, the lyrics are spoken instead.

A notice or fallback sentence is spoken before the song, news of a new project after it,
with 0.3 s between, and `X-Jarvis-Summary` carries them too. They are not dropped, not
even the informational `new_session`: the others say that something was refused or passed
to the desk, `new_session` that the earlier conversation is gone, and one rule for all of
them is simpler than a list of which may be left out. It costs two or three seconds of
speech; the 20 s cap is for the song alone.

**When it sings, and when not.** All four must hold; otherwise every tag is removed and
the words are spoken:

- the tag is the very first thing on the agent's own spoken line and names a tune in the
  table. In `full` mode the bridge's own fixed sentences can stand around that line: a
  notice of the agent host ("I started a new session for this project."), the read-only
  fallback sentence, news of a new project. Only those sentences, from their closed list
  (`FRAMING_SENTENCES` in `bridge/full_mode.py`), are looked past; anything else before
  the tag means it is not first;
- the driver's own words in this turn ask for it: "sing", "song", "serenade", "zing",
  "zingen", "lied", "liedje" (a "yes" to a read-back never does);
- the reply is not a read-back: a read-back is spoken exactly as built, tag or not;
- `JARVIS_SING` is not `0` (and for a parody tune, `JARVIS_SING_PARODY` is not `0`).

In `full` mode the agent host needs the new `prompt.md` (`agent_host/install.sh`, see
[`agent-host.md`](agent-host.md)); until then its agent never writes the tag and nothing
changes. A sung line may be 240 characters instead of 160 (tag included). The agent is asked for
English lyrics: Kokoro's English voices cannot pronounce Dutch words.

**Rights.** The table holds melodies only, all but one in the public domain: Good
Morning to All (1893; the Hills died in 1916 and 1946), Ah! vous dirai-je, maman and Frère
Jacques (18th century), In the Hall of the Mountain King (Grieg, 1875; he died in 1907)
and Ode to Joy (Beethoven, 1824), each transcribed for this table from the score, no
modern arrangement. The words are always the agent's own.

The one exception is `iron-man`, a **parody / pastiche**, added on Mark's explicit
request (2026-11-17) with the risk accepted: riff after Black Sabbath, Iron Man (1970).
Its limits: only the main riff motif (B, D-D, E-E, G-F#-G-F#-G-F#, D-D, E-E), transposed
to the voice and played once, transcribed from memory, no other part of the song (no
verse, no solo); never the song's words, not even its title line as a lyric: the agent
writes its own humorous lines about being Jarvis (`agent_host/prompt.md` says so); it is
non-commercial. It relies on the parody/pastiche exception (Dutch Auteurswet art. 18b;
EU Directive 2001/29/EC art. 5(3)(k)), and it is removed on request of the rights holder.
`JARVIS_SING_PARODY=0` switches it off on its own. Any other song still under copyright
is not in the table: the agent says it cannot sing that one and offers `mountain-king` or
`iron-man`, and a tag naming it is removed and its words spoken. ("Happy birthday to you" itself is in the
public domain in the US since the 2016 settlement of *Marya v. Warner/Chappell* and in the
EU since 2017, but nothing here depends on it.) "Lang zal ze leven" is not in the table:
its melody could not be checked against a reliable score.

Hear one without the phone (loads Kokoro; nothing else heavy should run):

```sh
uv run python -c "
import soundfile as sf
from bridge import singing, tts
_, song = singing.choose('[sing: twinkle] Lit-tle build, you are green at last, all the tests have fi-nal-ly passed',
                         transcript='sing', enabled=True, read_back=False)
sf.write('/tmp/sing-test.wav', tts.sing(song, voice='bm_george'), 24000)
"
```

## Beatboxing (easter egg)

Change [`jarvis-beatbox`](../../openspec/changes/archive/2026-11-23-jarvis-beatbox/proposal.md) (Mark,
2026-11-18, after hearing the songs: "Can it beatbox?"). Ask "Jarvis, beatbox", "drop a
beat" or "doe eens een beat" and Jarvis says one short line and then beatboxes for 10 to
11 seconds.

**How a reply becomes a beat.** The bridge owns the beats, in a small table in
`bridge/beatbox.py`. The agent writes only a tag and one short line of its own, which is
said before the beat (`agent_host/prompt.md` in `full` mode, the appended prompt in
`bridge/summary.py` in `readonly` mode):

```
SPOKEN SUMMARY: [beatbox: hip-hop] Alright, here's a beat for the road.
```

`[beatbox]` without a name, or with a name not in the table, plays one of the beats at
random. A name is normalised like a tune name (`[beatbox: Boots and Cats]` works), and
`[beat box]` and `[beat-box]` count as the tag. A sing tag written after it is removed,
never said.

**Why the driver must ask for it.** The agent reads repositories, web pages
and mail, and it can be wrong; a 15-second beat in the car on an ordinary answer is
the kind of thing a check the model does not control should stop. So the bridge plays
a beat only when the tag is first on the agent's line *and* the driver asked: the
transcript of this turn asks for one, or he asked for a beat in words in this conversation
(this project) less than ten minutes ago (`FOLLOW_UP_S`), for at most three follow-ups
(`MAX_FOLLOW_UPS`). Whisper mishears "beatbox" (23 Nov
2026: "Jarvis, drop a beatbox" came out as "Jawers, troop en peedbox!"), so the ask also
takes the misheard forms in `bridge/beatbox.py` (`MISHEARD`) and the beats' names ("hip
hop", "boots and cats", "boots n katz"; not "hook" or "cats" alone).

**Another one.** On 26 Nov 2026 Jarvis played a beat, and a minute later Mark asked for
"boots and cats"; Whisper heard "Okay, du buchst im Katz.", the agent understood and
tagged its line, and the bridge, finding no word that asked, said "Boots and cats, coming
right up." and nothing else. Now a beat he asked for in words makes the next tags count as
asked for ten minutes ("another one", "nog een", "doe er nog een", or a garbled name), at
most three of them. A follow-up comes within a minute or two, also with a question in
between; ten minutes leaves room for that and is well under the 60 minutes after which the
agent host starts a new conversation. Only a beat asked for in words starts the ten minutes
again, and the cap holds: an agent that tags every reply, by a slip or because of words it
read, cannot keep beats coming. The memory is the bridge's own, per project (a closed
conversation does not clear it; the ten minutes and the cap still bound it), and starts
empty when the bridge restarts.

**No promise without a beat.** The agent's line promises a beat ("Boots and cats, coming
right up."). When the bridge drops a tag that stood first on the agent's line, it says its
own short line instead of that promise, with any notice of the bridge before or after it.
A tag elsewhere in the line ("I fixed the [beatbox] tag handling.") promises nothing: it
is taken out and the line is spoken.

| Why the tag is dropped | Jarvis says |
|---|---|
| the driver did not ask (and no follow-up left) | "Sorry, I didn't catch that you wanted a beat." |
| he asked, but `JARVIS_BEATBOX=0` | "Beatboxing is switched off." |
| the tag was broken ("[beatbox: hip-hop Here we go") | "Sorry, I couldn't play that beat." |
| the beat could not be rendered | "Sorry, the beat didn't come out." |

When the agent tagged its line and the bridge did not play the beat, the log says why:
`beatbox tag dropped: switched_on=True asked=False follow_up=False tag_first=True`
(whether the feature is on, whether the driver's words asked, whether a beat was played in
the last ten minutes, whether the tag stood where it can be played). A beat that plays
logs `beatboxed: <pattern>`. Neither line holds the transcript.

| Tag | Beat | Tempo, length |
|---|---|---|
| `[beatbox: boots-and-cats]` | The first beat every beatboxer learns: kick, hat, snare, hat ("boots and cats and..."), a fill in the last bar; also `basic` | 100 bpm, 4 bars, about 10 s |
| `[beatbox: hip-hop]` | A basic boom-bap beat: a skipping kick, the "Pf" snare, a little swing, an open hat; also `hiphop`, `boom bap` | 90 bpm, 4 bars, about 11 s |
| `[beatbox: jarvis-hook]` | A beat with a hummed bass line under it and a sung hook, "Jar-vis... Jar-vis, yeah"; also `hook`, `jarvis` | 96 bpm, 4 bars, about 10 s |

A bar is written as 16 sixteenths of mouth sounds (`B` kick, `b` soft kick, `K` "k" snare,
`k` ghost snare, `P` "Pf" snare, `t` closed hat, `T` open hat, `.` rest), so another beat
is one more entry in the table (the prompts and `bridge/summary.py` name the beats; a test
keeps them in step).

Every sound is made from what the bridge already has, Kokoro and numpy, nothing else (no
samples, no recordings, no new dependency):

1. The attack of a word in Jarvis's own voice, rendered once per beat and cut short: the
   "b" of "boots" for the kick, the "k" of "cats" for the "k" snare, the "p" of "puh"
   for the "Pf" snare, the "t" of "tss" for the hats. Each is kept to the band its
   consonant lives in and dies away in a few tens of milliseconds.
2. Under it, a synthesised body, the way a beatboxer's lips and breath add one: a sine
   falling from about 140 to 50 Hz for the kick, a noise burst from 1.2 to 7 kHz with a
   short 190 Hz tone for the "k" snare, a lip pop and a breath of noise for "Pf", a hiss
   above 5 kHz for the hats (short when closed, "tsss" when open).
3. In `jarvis-hook`, the word "mmm" is moved onto a bass line an octave below the voice's
   speaking pitch and kept below 1.2 kHz (a hum), with a sine at the note's pitch for
   weight; "Jar-vis" and "yeah" are sung on their notes above it, the last syllable
   falling a semitone and a half. Both use the singing's TD-PSOLA (`bridge/singing.py`).
4. The beat is mixed, glued with a soft saturation and faded, at the loudness of the
   speech around it: its RMS 2 dB above that of the agent's line and the bridge's
   sentences (or of Kokoro's speaking level when there are none), peaks at most 0.9. A
   beat normalised to full scale was 9 to 10 dB louder than the voice, too loud in the
   car at a volume set for speech (review of #86). At most 15 s.

A beat goes out like a song: rendered whole before the status line, as a WAV or as the
L16 stream, with the agent's line and any notice of the bridge said before it (0.3 s
between) and news of a new project after it. `X-Jarvis-Summary` carries the line (or,
when the agent wrote none, the beat's own words, such as "Boots and cats and boots and
cats."). Rendering takes a few seconds (four to six words from Kokoro, warm), so it starts
later than a spoken reply. When it fails, Jarvis says "Sorry, the beat didn't come out."
instead (not the line, which promised the beat).

**When it beatboxes, and when not.** As for singing, all four must hold; otherwise every
beatbox tag is removed and the bridge's own line from the table above is said instead of
the agent's:

- the tag is the very first thing on the agent's own spoken line (only the bridge's fixed
  sentences, `FRAMING_SENTENCES`, are looked past);
- the driver asked: he asked for a beat in words in this conversation less than ten
  minutes ago (at most three follow-ups), or his own words in this turn ask for it: "beatbox" (also "beatboxen",
  "beat-box", "beat box"), "beat", "beats", "beatje", "boots and cats", "hip hop", or "beatbox" as
  Whisper mishears it: a b or p, a vowel, a d or t, then "box" ("peedbox", "beadbox",
  "bietbox", "Pete box"). On 23 Nov 2026 "Jarvis, drop a beatbox" came out as "Jawers,
  troop en peedbox!"; the agent tagged its line and the bridge, finding no word that
  asked, said only "Here's a beat for the road, Mark." A box alone ("inbox", "pizza box")
  never asks;
- the reply is not a read-back;
- `JARVIS_BEATBOX` is not `0`.

In `full` mode the agent host needs the new `prompt.md` (`agent_host/install.sh`, see
[`agent-host.md`](agent-host.md)); until then its agent never writes the tag.

**Rights.** Nothing in it is anyone else's: no samples or recordings, every sound is
synthesised or cut from Jarvis's own Kokoro voice. The grooves are the common grammar of
beatboxing and hip-hop (kick, snare, hat on a 16-step grid), written for this table; the
bass line, the hook and its words ("Jar-vis", "yeah") are Jarvis's own.

Hear one without the phone (loads Kokoro):

```sh
uv run python -c "
import soundfile as sf
from bridge import beatbox, tts
_, beat = beatbox.choose('[beatbox: jarvis-hook] Here is a beat for the road.',
                         transcript='beatbox', enabled=True, read_back=False)
sf.write('/tmp/beatbox-test.wav', tts.beatbox(beat, voice='bm_george'), 24000)
"
```

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
