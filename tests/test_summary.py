import json
from pathlib import Path

import pytest

from bridge.summary import (
    FALLBACK_SUMMARY,
    SUMMARY_MARKER,
    extract_summary,
    speakable,
    spoken_line,
)


def test_extracts_trailing_marker_line():
    reply = (
        "Here is the change:\n"
        "```python\nprint('hi')\n```\n"
        f"{SUMMARY_MARKER} Added a print statement.\n"
    )
    assert extract_summary(reply) == "Added a print statement."


def test_ignores_marker_text_inside_a_code_block():
    reply = f"```\n{SUMMARY_MARKER} not this one\n```\n{SUMMARY_MARKER} Real summary."
    assert extract_summary(reply) == "Real summary."


def test_without_a_marker_the_reply_itself_is_spoken():
    assert extract_summary("Just a plain reply with no marker.") == (
        "Just a plain reply with no marker.")


def test_with_an_empty_marker_line_the_reply_above_it_is_spoken():
    assert extract_summary(f"Some reply.\n{SUMMARY_MARKER}   ") == "Some reply."
    assert extract_summary(f"{SUMMARY_MARKER}   ") == FALLBACK_SUMMARY


def test_accepts_a_bold_marker_and_strips_markdown():
    assert extract_summary("Reply.\n**SPOKEN SUMMARY:** All *tests* pass.") == "All tests pass."


def test_reads_the_next_line_when_the_marker_stands_alone():
    assert extract_summary(f"Reply.\n{SUMMARY_MARKER}\nThe build is green.") == "The build is green."


@pytest.mark.parametrize("bad,said", [
    ("Changed /home/mark/code/demo/app.py.", "Changed app."),
    ("Run `rm -rf build && make` next.", "Reply."),
    ("Edited bridge/server.py and the tests.", "Edited server and the tests."),
    ("Set x = {a: 1}.", "Reply."),
])
def test_never_speaks_paths_or_code(bad, said):
    # A path is said as its name; a sentence with code is left out, and then the reply
    # above the marker is spoken.
    assert extract_summary(f"Reply.\n{SUMMARY_MARKER} {bad}") == said
    assert extract_summary(f"{SUMMARY_MARKER} {bad}") in (said, FALLBACK_SUMMARY)


def test_a_very_long_summary_is_shortened_not_dropped():
    spoken = extract_summary(f"{SUMMARY_MARKER} " + "word " * 200)
    assert spoken != FALLBACK_SUMMARY and len(spoken) <= 160


# Review of run-bridge-in-cluster: nothing shaped like a credential is spoken.

def test_never_speaks_an_anthropic_key():
    for bad in (
        "The token is sk-ant-oat01-abc.",
        "It starts with SK-ANT- and then some letters.",
    ):
        assert extract_summary(f"{SUMMARY_MARKER} {bad}") == FALLBACK_SUMMARY, bad
        said = extract_summary(f"The key is sk-ant-oat01-abc\n{SUMMARY_MARKER} {bad}")
        assert said == FALLBACK_SUMMARY, bad
        assert extract_summary(f"{SUMMARY_MARKER} All good. {bad}") == "All good."


def test_never_speaks_a_long_token_like_run():
    hex_token = "a3f9" * 8                       # 32 characters
    assert extract_summary(f"{SUMMARY_MARKER} The value is {hex_token} here.") == FALLBACK_SUMMARY
    b64ish = "Zm9vYmFy-ZmFrZV9zZWNyZXQ_dmFsdWU1234"
    assert extract_summary(f"{SUMMARY_MARKER} Found {b64ish} in the file.") == FALLBACK_SUMMARY
    # Not in a path's name either.
    assert extract_summary(f"Wrote /tmp/{hex_token}.json for you.") == FALLBACK_SUMMARY


def test_still_speaks_ordinary_long_words():
    reply = f"{SUMMARY_MARKER} Internationalization and the counterrevolutionaries both pass."
    assert extract_summary(reply) == "Internationalization and the counterrevolutionaries both pass."


def test_the_spoken_line_is_capped_at_160_characters():
    from bridge.summary import MAX_SPOKEN_CHARS

    assert MAX_SPOKEN_CHARS == 160
    fits = ("word " * 32).strip()                # 159 characters
    assert len(fits) <= MAX_SPOKEN_CHARS
    assert extract_summary(f"{SUMMARY_MARKER} {fits}") == fits


def test_a_long_summary_is_cut_at_the_last_sentence_end_within_the_cap():
    first = "The build is green and the image was pushed."
    second = "Two tests were added for the voice!"
    third = "The rest of this reply goes on " + "and on " * 20 + "for far too long."
    spoken = extract_summary(f"{SUMMARY_MARKER} {first} {second} {third}")
    assert spoken == f"{first} {second}"
    # A full stop inside a number is not a sentence end.
    spoken = extract_summary(f"{SUMMARY_MARKER} Version 2.5 is out. " + "word " * 40)
    assert spoken == "Version 2.5 is out."


def test_a_long_summary_without_a_sentence_end_is_cut_at_a_word_with_an_ellipsis():
    spoken = extract_summary(f"{SUMMARY_MARKER} " + "word " * 200)
    assert spoken == ("word " * 32).strip() + "…"      # 159 characters and the ellipsis
    assert len(spoken) <= 160
    spoken = extract_summary(f"{SUMMARY_MARKER} " + "sevench, " * 30)
    assert spoken.endswith("sevench…") and len(spoken) <= 160


def test_a_long_summary_the_filters_reject_is_made_speakable_whole():
    # The path and the token sit beyond the cap: the cut would drop them, the filter must
    # not. The summary is made speakable as a whole, and then cut.
    long_start = "All good. " + "word " * 40
    assert extract_summary(f"{SUMMARY_MARKER} {long_start} see src/app/main.py") == "All good."
    assert extract_summary(f"{SUMMARY_MARKER} {long_start} " + "a3f9" * 8) == "All good."


def test_the_instruction_asks_for_one_short_sentence():
    from bridge.summary import SYSTEM_PROMPT_ADDITION

    assert "one sentence, two at most" in SYSTEM_PROMPT_ADDITION
    assert "100 to 150 characters" in SYSTEM_PROMPT_ADDITION


# --- Mark, 26 Nov 2026: "and he says check the log for details, but cant show them" ----

def test_the_fallback_never_mentions_a_log():
    assert "log" not in FALLBACK_SUMMARY.lower()
    assert FALLBACK_SUMMARY == ("Done, but I couldn't put the answer into words. "
                                "Ask me to say it again.")


# Turn 36754996 (26 Nov 2026, 10:20): the summary was speakable, but held a semicolon,
# which the filter took for code; Mark heard the fallback. 29 of 165 recorded turns did.
CLUSTER_SUMMARY = (
    "All green: six nodes ready, all sixteen Argo apps synced and healthy, and nothing "
    "failing; the only blips were Keycloak restarting about half an hour ago, which is "
    "fine now, plus the usual DNS warning noise.")


def test_a_semicolon_is_spoken_not_taken_for_code():
    assert extract_summary(f"{SUMMARY_MARKER} The gate refused it; nothing ran.") == (
        "The gate refused it; nothing ran.")


def test_a_long_summary_with_a_semicolon_is_cut_at_the_semicolon():
    assert extract_summary(f"Reply.\n{SUMMARY_MARKER} {CLUSTER_SUMMARY}") == (
        "All green: six nodes ready, all sixteen Argo apps synced and healthy, and "
        "nothing failing.")


CLUSTER_REPLY = """The cluster is healthy and there's no incident right now.

- **Nodes:** all 6 are Ready.
- **Argo CD:** all 16 apps are Synced and Healthy.

**Recent warnings, all resolved:**
- **`edge-test` pod in keycloak (45 min ago):** its container kept failing.

```bash
kubectl get pods -A | grep -v Running
```

| app | state |
|---|---|
| keycloak | Healthy |

Details: https://argocd.example/applications and ~/homelab/cluster-config/infra/jarvis/deployment.yaml.
"""


def test_without_a_summary_line_the_reply_is_made_speakable():
    said = extract_summary(CLUSTER_REPLY)
    assert said == ("The cluster is healthy and there's no incident right now. "
                    "Nodes: all 6 are Ready. Argo CD: all 16 apps are Synced and Healthy. "
                    "Recent warnings, all resolved.")
    whole = speakable(CLUSTER_REPLY)
    for gone in ("**", "`", "kubectl", "|", "https", "/", "- "):
        assert gone not in whole, gone
    assert "Recent warnings, all resolved." in whole
    assert "edge-test pod in keycloak (45 min ago): its container kept failing." in whole
    assert "Details: the link and deployment." in whole


@pytest.mark.parametrize("text,said", [
    ("See https://github.com/MWest2020/jarvis/pull/5 for details.", "See the link for details."),
    ("Merged [PR 5](https://x.y/z) into `main`.", "Merged PR 5 into main."),
    ("1. First step done\n2. Second step done", "First step done. Second step done."),
    ("# Status\nAll fine", "Status. All fine."),
    ("> quoted line", "quoted line."),
    ("The repo MWest2020/jarvis is green.", "The repo MWest2020 jarvis is green."),
    ("Edited `server.py`.", "Edited server."),
])
def test_markup_urls_and_paths_become_words(text, said):
    assert extract_summary(text) == said


def test_only_code_leaves_the_honest_line():
    for reply in ("```\nprint('hi')\n```", "`rm -rf /`", "", "   ", "| a | b |\n|---|---|",
                  "sk-ant-oat01-xyz"):
        assert extract_summary(reply) == FALLBACK_SUMMARY, reply


def test_a_long_reply_is_cut_to_the_spoken_length():
    reply = "This sentence is the first one of many. " * 20
    said = extract_summary(reply)
    assert len(said) <= 160 and said.endswith(".")


def test_the_session_close_line_is_never_spoken():
    assert extract_summary("Goodbye, Mark.\nSESSION: close") == "Goodbye, Mark."


# The agent host sends only one line (D33): the summary line, or now its own line made
# from the reply, so `full` mode speaks what read-only mode speaks.

def test_spoken_line_is_the_marker_line_when_it_says_it():
    reply = f"Long reply.\n{SUMMARY_MARKER} The thing is done."
    assert spoken_line(reply) == f"{SUMMARY_MARKER} The thing is done."
    tagged = f"{SUMMARY_MARKER} [beatbox: hip-hop] Here we go."
    assert spoken_line(tagged) == tagged


@pytest.mark.parametrize("reply", [
    CLUSTER_REPLY,
    "Just a plain reply with no marker.",
    f"Reply above.\n{SUMMARY_MARKER} Run `rm -rf build && make` next.",
    f"Some reply.\n{SUMMARY_MARKER}   ",
    f"{SUMMARY_MARKER} Changed /home/mark/app.py.",
    "```\ncode only\n```",
    "",
])
def test_spoken_line_gives_the_bridge_what_the_whole_reply_gives(reply):
    line = spoken_line(reply)
    assert "\n" not in line
    assert line == "" or line.startswith(SUMMARY_MARKER)
    assert extract_summary(line) == extract_summary(reply)
    assert line == "" or line == f"{SUMMARY_MARKER} {extract_summary(reply)}"


def test_replays_of_the_recorded_turns_never_fall_back_on_a_speakable_summary():
    # The records the agent host keeps, when they are on this machine (not in CI).
    records = Path.home() / ".local/state/jarvis-agent/records"
    if not records.is_dir():
        pytest.skip("no agent records here")
    for path in records.glob("*.json"):
        record = json.loads(path.read_text())
        if record.get("outcome") == "ok" and record.get("reply"):
            assert extract_summary(record["reply"]) != FALLBACK_SUMMARY, path.name



# Review of #104: what speakable() makes of text must still pass the spoken filter.

@pytest.mark.parametrize("bad", [
    "I set the key to wJalrXUtnFEMIK7MDENGb/PxRfiCYEXAMPLEKEYab12 for you.",
    "Secret wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY set.",
    "Connect with postgres://admin:S3cretPass@db.internal:5432/app please.",
    "See redis://:hunter2pass@cache:6379/0 now.",
    "Clone git@github.com:MWest2020/jarvis.git",
    "The commit is 3f2a9c1d4e5f6a7b8c9d.",
    "Use `ghp_A1b2C3d4E5f6G7h8` as the token.",
    "It is at host.example.com/path?token=abcdef123456 now.",
])
def test_a_secret_never_survives_being_made_speakable(bad):
    for reply in (f"Body text.\n{SUMMARY_MARKER} {bad}", bad):
        said = extract_summary(reply)
        line = spoken_line(reply)
        for secret in ("PxRfi", "S3cret", "hunter2", "git@", "bPxRf", "3f2a9c", "ghp_",
                       "abcdef123456"):
            assert secret not in line and secret not in said


@pytest.mark.parametrize("bad", [
    "    for i in range(10): print(i)\nThat loops.",
    "\tx = compute()\nThat loops.",
    "def main(argv): return run(argv).\nThat loops.",
    "Run git push --force origin main to publish.\nThat loops.",
    "Run cd /tmp; rm -rf build to clean up.\nThat loops.",
    "Run import os; os.remove(p) first.\nThat loops.",
    "import os\nThat loops.",
    "Set retries = 3 in the config.\nThat loops.",
])
def test_code_in_the_body_is_never_spoken(bad):
    assert extract_summary(bad) == "That loops."


def test_a_command_with_a_semicolon_in_the_summary_is_not_spoken():
    for bad in ("Run cd /tmp; rm -rf build to clean up.", "Run import os; os.remove(p) first."):
        assert extract_summary(f"Reply.\n{SUMMARY_MARKER} {bad}") == "Reply."


def test_home_and_root_paths_are_said_as_names():
    assert extract_summary("I put it in ~/.env and /etc for you.") == (
        "I put it in env and etc for you.")


def test_plain_words_in_brackets_are_no_call():
    assert extract_summary("Updated the test(s) and the doc(es).") == (
        "Updated the test(s) and the doc(es).")
    assert extract_summary("The build (on main) passed.") == "The build (on main) passed."


def test_a_nested_list_item_is_no_code_block():
    assert extract_summary("Done:\n- first\n    - second item") == "Done. first. second item."


@pytest.mark.parametrize("pathological", [
    "a-" * 10000, " \n" * 20000, "-" * 20000, "\n" * 20000, "a/" * 10000, "a." * 10000,
    "```\n" + "x\n" * 10000, "word " * 20000,
])
def test_a_pathological_reply_is_handled_fast(pathological):
    import time
    started = time.monotonic()
    extract_summary(pathological)
    spoken_line(pathological)
    assert time.monotonic() - started < 1.0
