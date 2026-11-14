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
