# Change: jarvis-say-it-honestly

## Why

Mark, 26 Nov 2026, from the car:

- "jarvis did a beatbox, but when prompted for another it stayed silent." Turn bcd0c440
  played `jarvis-hook`. 63 seconds later Whisper made his English "boots and cats" into
  "Okay, du buchst im Katz." (turn 4075c2e0). The agent understood and tagged its line, but
  no word in the transcript asked for a beat, so the bridge dropped the tag
  (`beatbox tag dropped: switched_on=True asked=False tag_first=True`) and said "Boots and
  cats, coming right up." with nothing after it: a promise it did not keep.
- "and he says check the log for details, but cant show them". The fallback for a reply
  without a speakable summary was "Done. Check the log for details." He is on a phone. The
  fallback fired in 29 of 165 recorded turns, every time for a semicolon in a summary that
  was otherwise fine ("...nothing failing; the only blips were..."): the filter took `;`
  for code.

He asked for both directly, so they are built in the same PR as this proposal; the PR is
the approval point, as for `jarvis-beatbox`.

## What Changes

- **A follow-up counts as asked.** A beat asked for in words in this conversation (this
  project) less than ten minutes ago makes the next beatbox tags count as asked ("another
  one", "nog een", a garbled transcript), at most three of them; a follow-up does not
  restart the ten minutes. The beats' names ("hip hop", "boots and cats") also ask. Still
  never unprompted: the tag must be first on the agent's line, never a read-back, a beat
  must have been asked for in words first, and an agent that tags every reply gets at most
  three beats.
- **No promise without a beat.** When the bridge drops a beatbox tag that stood first on
  the agent's line, it says a short line of its own instead of the agent's line (a tag
  elsewhere is taken out and the line spoken, as before) ("Sorry, I didn't catch that you wanted a beat.",
  "Beatboxing is switched off.", "Sorry, I couldn't play that beat."); when a beat cannot be
  rendered, "Sorry, the beat didn't come out."
- **The answer, not a pointer to a log.** A semicolon is speech. A summary with code in it
  is made speakable (paths as names, URLs as "the link", sentences with code left out);
  without a summary line, the reply's own first sentences are said, made speakable the same
  way. Only when nothing speakable is left: "Done, but I couldn't put the answer into words.
  Ask me to say it again." Never a log.
- **One filtered line leaves the agent host.** In `full` mode the agent host sends a
  summary line with exactly what the bridge makes of the whole reply
  (`summary.spoken_line`): the agent's summary when speakable as it is, else a filtered
  excerpt of the reply, at most 160 characters, else nothing. Changes D33 of
  `jarvis-full-agent` in that respect. The bridge speaks only the summary line of the field.
- **The driver's languages (opt-in).** `JARVIS_WHISPER_LANGUAGES=nl,en`: Whisper's language
  detection picks the likeliest of these only. Unset: as before.

## Impact

- Modified requirements in `voice-bridge`: Beatboxing on request, Spoken output.
- Affected code: `bridge/beatbox.py`, `bridge/server.py`, `bridge/summary.py`,
  `bridge/remote_adapter.py`, `bridge/stt.py`, `bridge/config.py`, `agent_host/wrapper.py`.
- The bridge goes first; the agent host's part (its own line when the summary is missing)
  needs `agent_host/install.sh`. An old agent host with the new bridge: as before, plus the
  semicolon and the honest fallback.
- How it works: [`docs/how-to/bridge-service.md`](../../../docs/how-to/bridge-service.md).
