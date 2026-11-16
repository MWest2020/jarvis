from bridge.summary import FALLBACK_SUMMARY, SUMMARY_MARKER, extract_summary


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


def test_falls_back_when_marker_is_missing():
    assert extract_summary("Just a plain reply with no marker.") == FALLBACK_SUMMARY


def test_falls_back_when_marker_line_is_empty():
    assert extract_summary(f"Some reply.\n{SUMMARY_MARKER}   ") == FALLBACK_SUMMARY


def test_accepts_a_bold_marker_and_strips_markdown():
    assert extract_summary("Reply.\n**SPOKEN SUMMARY:** All *tests* pass.") == "All tests pass."


def test_reads_the_next_line_when_the_marker_stands_alone():
    assert extract_summary(f"Reply.\n{SUMMARY_MARKER}\nThe build is green.") == "The build is green."


def test_never_speaks_paths_or_code():
    for bad in (
        "Changed /home/mark/code/demo/app.py.",
        "Run `rm -rf build && make` next.",
        "Edited bridge/server.py and the tests.",
        "Set x = {a: 1}.",
    ):
        assert extract_summary(f"Reply.\n{SUMMARY_MARKER} {bad}") == FALLBACK_SUMMARY, bad


def test_never_speaks_a_very_long_summary():
    assert extract_summary(f"{SUMMARY_MARKER} " + "word " * 200) == FALLBACK_SUMMARY


# Review of run-bridge-in-cluster: nothing shaped like a credential is spoken.

def test_never_speaks_an_anthropic_key():
    for bad in (
        "The token is sk-ant-oat01-abc.",
        "It starts with SK-ANT- and then some letters.",
    ):
        assert extract_summary(f"Reply.\n{SUMMARY_MARKER} {bad}") == FALLBACK_SUMMARY, bad


def test_never_speaks_a_long_token_like_run():
    hex_token = "a3f9" * 8                       # 32 characters
    assert extract_summary(f"{SUMMARY_MARKER} The value is {hex_token} here.") == FALLBACK_SUMMARY
    b64ish = "Zm9vYmFy-ZmFrZV9zZWNyZXQ_dmFsdWU1234"
    assert extract_summary(f"{SUMMARY_MARKER} Found {b64ish} in the file.") == FALLBACK_SUMMARY


def test_still_speaks_ordinary_long_words():
    reply = f"{SUMMARY_MARKER} Internationalization and the counterrevolutionaries both pass."
    assert extract_summary(reply) == "Internationalization and the counterrevolutionaries both pass."


def test_the_spoken_line_is_capped_at_160_characters():
    from bridge.summary import MAX_SPOKEN_CHARS

    assert MAX_SPOKEN_CHARS == 160
    fits = ("word " * 32).strip()                # 159 characters
    assert len(fits) <= MAX_SPOKEN_CHARS
    assert extract_summary(f"{SUMMARY_MARKER} {fits}") == fits
    too_long = ("word " * 33).strip()            # 164 characters
    assert extract_summary(f"{SUMMARY_MARKER} {too_long}") == FALLBACK_SUMMARY


def test_the_instruction_asks_for_one_short_sentence():
    from bridge.summary import SYSTEM_PROMPT_ADDITION

    assert "one sentence, two at most" in SYSTEM_PROMPT_ADDITION
    assert "100 to 150 characters" in SYSTEM_PROMPT_ADDITION
