"""The two sides of the wire, held against each other (jarvis-full-agent D33):
what the bridge sends is read by the agent host's own request parser, and what
the agent host's own reply builder writes is read by the bridge's parser. No
process, no connection: only the two shapes."""
import json
import uuid

import pytest

from agent_host import request as wrapper_request
from agent_host import wrapper
from bridge import remote_adapter
from bridge.agent import ConfirmAnswer
from bridge.remote_adapter import build_request, parse_reply

SHA = "ab" * 32


def test_the_wrapper_reads_the_bridges_request():
    req = wrapper_request.parse_request(
        build_request(project="demo", prompt="what is open?", confirm=None))
    assert (req.project, req.prompt, req.confirm) == ("demo", "what is open?", None)


@pytest.mark.parametrize("answer", ["yes", "no"])
def test_the_wrapper_reads_the_bridges_answer(answer):
    req = wrapper_request.parse_request(
        build_request(project="demo", prompt="", confirm=ConfirmAnswer("c1", SHA, answer)))
    assert req.prompt == ""
    assert (req.confirm.id, req.confirm.sha256, req.confirm.answer) == ("c1", SHA, answer)


def test_both_sides_have_the_same_fields_errors_and_notices():
    assert set(wrapper.V1_FIELDS) == remote_adapter.REPLY_KEYS
    assert wrapper.ERROR_CODES == remote_adapter.ERROR_CODES
    assert wrapper.NOTICE_CODES == set(remote_adapter.NOTICE_SENTENCES)


def _wire(reply: dict) -> bytes:
    return json.dumps(reply).encode()


def test_the_bridge_reads_every_reply_the_wrapper_builds():
    turn = str(uuid.uuid4())                      # the wrapper's turn ids are UUIDs
    ok = parse_reply(_wire(wrapper._reply(status="ok", turn=turn,
                                          spoken="SPOKEN SUMMARY: Done.")))
    assert (ok["turn"], ok["spoken"]) == (turn, "SPOKEN SUMMARY: Done.")
    assert parse_reply(_wire(wrapper.disabled_reply()))["error"] == "disabled"
    for code in wrapper.ERROR_CODES:
        assert parse_reply(_wire(wrapper._reply(status="error", error=code)))["error"] == code
    for notice in wrapper.NOTICE_CODES:
        parsed = parse_reply(_wire(wrapper._reply(status="ok", turn=turn, notice=notice)))
        assert parsed["notice"] == notice
    pending = parse_reply(_wire(wrapper._reply(
        status="ok", turn=turn, pending=True, readback="Merge pull request 21 on jarvis.",
        confirm={"id": "c1", "sha256": SHA, "class": "merge", "expires_s": 60})))
    assert pending["confirmation"].sha256 == SHA
