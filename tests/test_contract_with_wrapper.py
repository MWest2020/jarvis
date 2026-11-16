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


def test_both_sides_accept_the_same_project_names():
    from agent_host import projects
    assert projects._PROJECT_NAME.pattern == remote_adapter.PROJECT_NAME.pattern
    assert projects._PROJECT_NAME.flags == remote_adapter.PROJECT_NAME.flags


def test_both_sides_cap_a_message_at_the_same_size():
    assert wrapper_request.MAX_REQUEST_BYTES == remote_adapter.MAX_MESSAGE_BYTES


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


# --- phase 1b: the real wrapper's read-back and confirm path, read by the bridge ----------
#
# The fake `claude` hands its tool calls to the real gate (tests/
# test_agent_host_wrapper_confirm.py, `Host`); everything else on the agent
# host's side is the code that will be installed. What it writes is read by
# the bridge's parser, and what the bridge then builds is read by the wrapper.

from agent_host import gatehash  # noqa: E402
from bridge.remote_adapter import speakable_readback  # noqa: E402
from tests.test_agent_host_wrapper_confirm import MERGE, MERGE_SENTENCE, Host  # noqa: E402


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


def _through_the_bridge(host, *, prompt, confirm=None):
    """One request as the bridge builds it, one reply as the bridge reads it."""
    reply = wrapper.handle_request(
        build_request(project="demo", prompt=prompt, confirm=confirm), host.config)
    assert set(reply) == remote_adapter.REPLY_KEYS
    return reply, parse_reply(_wire(reply))


def test_the_bridge_reads_the_real_wrappers_read_back(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    reply, parsed = _through_the_bridge(host, prompt="merge it")

    assert parsed["spoken"] == "" and parsed["error"] == "" and parsed["notice"] == ""
    assert parsed["readback"] == f"{MERGE_SENTENCE} Checks green, review approved. Say yes to go ahead."
    # Spoken as built: the bridge's own check of a read-back accepts it unchanged.
    assert speakable_readback(parsed["readback"]) == parsed["readback"]
    assert 0 < parsed["expires_s"] <= remote_adapter.MAX_EXPIRES_S
    confirmation = parsed["confirmation"]
    assert confirmation.id == reply["confirm"]["id"]
    assert confirmation.sha256 == gatehash.call_hash(
        "Bash", str(host.checkout.resolve()), {"command": MERGE})
    assert set(reply["confirm"]) == remote_adapter.CONFIRM_KEYS


def test_the_bridges_yes_releases_the_call_on_the_real_wrapper(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    _, asked = _through_the_bridge(host, prompt="merge it")
    pending = asked["confirmation"]

    host.agent(monkeypatch, [MERGE], reply="merged.\nSPOKEN SUMMARY: It is merged.")
    reply, parsed = _through_the_bridge(
        host, prompt="", confirm=ConfirmAnswer(pending.id, pending.sha256, "yes"))
    assert parsed["spoken"] == "SPOKEN SUMMARY: It is merged."
    assert parsed["confirmation"] is None
    assert host.popens[-1].decisions[0].allow
    assert host.popens[-1].kwargs["env"]["JARVIS_GRANT"] == pending.sha256
    # Single use: the same answer again finds nothing, and the bridge reads that too.
    _, again = _through_the_bridge(
        host, prompt="", confirm=ConfirmAnswer(pending.id, pending.sha256, "yes"))
    assert again["error"] == "expired"


def test_the_bridges_no_clears_the_call_on_the_real_wrapper(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    _, asked = _through_the_bridge(host, prompt="merge it")
    pending = asked["confirmation"]
    reply, parsed = _through_the_bridge(
        host, prompt="", confirm=ConfirmAnswer(pending.id, pending.sha256, "no"))
    assert (reply["status"], reply["turn"], parsed["error"]) == ("ok", None, "")
    assert not host.pending_file().exists()
    _, late = _through_the_bridge(
        host, prompt="", confirm=ConfirmAnswer(pending.id, pending.sha256, "yes"))
    assert late["error"] == "expired"


def test_a_yes_to_a_busy_host_keeps_the_pending_call_for_the_bridge_to_ask_again(host, monkeypatch):
    from agent_host.turnlock import acquire_turn_lock
    host.agent(monkeypatch, [MERGE])
    _, asked = _through_the_bridge(host, prompt="merge it")
    pending = asked["confirmation"]
    before = host.pending_file().read_text()
    answer = ConfirmAnswer(pending.id, pending.sha256, "yes")

    with acquire_turn_lock(host.config.lock_path):
        _, busy = _through_the_bridge(host, prompt="", confirm=answer)
    assert busy["error"] == "busy"
    monkeypatch.setattr(wrapper, "available_memory_mb", lambda: 1.0)
    _, low = _through_the_bridge(host, prompt="", confirm=answer)
    assert low["error"] == "low_memory"
    assert host.pending_file().read_text() == before          # same id, same expiry

    monkeypatch.setattr(wrapper, "available_memory_mb", lambda: 10_000.0)
    host.agent(monkeypatch, [MERGE])
    _, done = _through_the_bridge(host, prompt="", confirm=answer)
    assert done["error"] == "" and host.popens[-1].decisions[0].allow


@pytest.mark.parametrize("calls,notice", [
    (["terraform apply"], "needs_desk"),
    ([MERGE + " && echo done"], "run_alone"),
])
def test_the_bridge_has_a_sentence_for_every_notice_the_real_gate_leads_to(host, monkeypatch, calls, notice):
    host.agent(monkeypatch, calls)
    reply, parsed = _through_the_bridge(host, prompt="do it")
    assert parsed["notice"] == notice
    assert notice in remote_adapter.NOTICE_SENTENCES
    assert parsed["confirmation"] is None and parsed["readback"] == ""


def test_every_class_string_the_wrapper_sends_is_one_the_bridge_accepts(host, monkeypatch):
    for command, classes in (
        ("sudo kubectl delete pod web -n shop --context homelab --force", "root, delete, force"),
        (f"sudo {host.config.confirm_program} off", "self"),
        ("scp notes.txt web.example.org:/srv/", "outward"),
    ):
        host.agent(monkeypatch, [command])
        reply, parsed = _through_the_bridge(host, prompt="do it")
        assert reply["confirm"]["class"] == classes
        assert parsed["confirmation"] is not None
        assert speakable_readback(parsed["readback"]) == parsed["readback"]


def test_both_sides_agree_on_the_length_of_a_read_back_and_the_60_seconds():
    from agent_host.config import PENDING_SECONDS
    assert wrapper.MAX_READBACK_CHARS == remote_adapter.MAX_READBACK_CHARS
    assert PENDING_SECONDS == remote_adapter.MAX_EXPIRES_S


# --- jarvis-general-conversation: the list request and the new project (D51, D53) ----------

from agent_host import projects as wrapper_projects  # noqa: E402
from bridge.remote_adapter import (  # noqa: E402
    ListRefused,
    build_list_request,
    parse_list_reply,
)


def test_the_wrapper_reads_the_bridges_list_request():
    assert isinstance(wrapper_request.parse_request(build_list_request()),
                      wrapper_request.ListRequest)


def test_both_sides_agree_on_the_list_answer():
    assert set(wrapper.LIST_FIELDS) == remote_adapter.LIST_REPLY_KEYS
    assert wrapper_projects.MAX_LISTED == remote_adapter.MAX_LISTED
    assert parse_list_reply(_wire(wrapper.list_reply(["b", "a"]))) == ["a", "b"]
    assert parse_list_reply(_wire(wrapper.list_reply([]))) == []


@pytest.mark.parametrize("reply,code", [
    (wrapper.disabled_reply(), "disabled"),
    (wrapper._reply(status="error", error="bad_request"), "bad_request"),
])
def test_the_bridge_reads_a_refused_list(reply, code):
    with pytest.raises(ListRefused) as refused:
        parse_list_reply(_wire(reply))
    assert refused.value.code == code


def test_the_real_wrapper_lists_for_the_bridge(host):
    reply = wrapper.handle_request(build_list_request(), host.config)
    assert parse_list_reply(_wire(reply)) == ["demo"]


def test_an_old_wrapper_refuses_the_list_as_bad_request():
    """What a wrapper from before D51 does with the list: an unknown field."""
    old = {"v", "project", "prompt", "confirm"}
    assert set(json.loads(build_list_request())) - old      # it is not a turn request


def test_the_bridge_reads_the_real_wrappers_new_project(tmp_path, monkeypatch):
    from tests.test_agent_host_general import CREATE, CloneHost
    host = CloneHost(tmp_path)
    host.agent(monkeypatch, [CREATE])
    _, asked = _through_the_bridge(host, prompt="make a repo called birdsong")
    pending = asked["confirmation"]
    host.agent(monkeypatch, [CREATE], reply="made.\nSPOKEN SUMMARY: I created birdsong.")
    _, parsed = _through_the_bridge(
        host, prompt="", confirm=ConfirmAnswer(pending.id, pending.sha256, "yes"))
    assert parsed["notice"] == "project_ready"
    assert remote_adapter._spoken(parsed["notice"], parsed["spoken"]) == \
        "I created birdsong. It's ready. You can switch to it."
    listed = parse_list_reply(_wire(wrapper.handle_request(build_list_request(), host.config)))
    assert "birdsong" in listed
