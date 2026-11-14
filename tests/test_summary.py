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
