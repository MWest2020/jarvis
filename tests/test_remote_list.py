"""The bridge's side of the list request (jarvis-general-conversation D51, D52):
the answer's two shapes, and FullMode's choice between the agent host's list and
the bridge's own."""
import json

import pytest

from bridge import full_mode, remote_adapter
from bridge.agent import AgentTurnFailed, AgentUnavailable
from bridge.full_mode import FullMode
from bridge.remote_adapter import ListRefused, ReplyError, build_list_request, parse_list_reply

ERROR_REPLY = {"v": 1, "status": "error", "error": "disabled", "turn": None, "spoken": "",
               "pending": False, "readback": None, "confirm": None, "notice": None}


def _raw(obj) -> bytes:
    return json.dumps(obj).encode()


def test_the_list_request_is_exactly_two_fields():
    assert json.loads(build_list_request()) == {"v": 1, "list": True}


def test_a_list_answer_is_read_sorted_and_without_doubles():
    raw = _raw({"v": 1, "status": "ok", "error": None, "projects": ["b", "a", "b"]})
    assert parse_list_reply(raw) == ["a", "b"]


@pytest.mark.parametrize("reply", [
    {"v": 1, "status": "ok", "error": None},                                   # no projects
    {"v": 1, "status": "ok", "error": None, "projects": ["a"], "extra": 1},
    {"v": 2, "status": "ok", "error": None, "projects": []},
    {"v": True, "status": "ok", "error": None, "projects": []},
    {"v": 1, "status": "error", "error": None, "projects": []},
    {"v": 1, "status": "ok", "error": "busy", "projects": []},
    {"v": 1, "status": "ok", "error": None, "projects": "a"},
    {"v": 1, "status": "ok", "error": None, "projects": ["../etc"]},
    {"v": 1, "status": "ok", "error": None, "projects": [".hidden"]},
    {"v": 1, "status": "ok", "error": None, "projects": [1]},
    {"v": 1, "status": "ok", "error": None,
     "projects": [f"p{i}" for i in range(remote_adapter.MAX_LISTED + 1)]},
    dict(ERROR_REPLY, status="ok", error=None),                                # a turn reply
    [],
])
def test_anything_else_is_not_a_list(reply):
    with pytest.raises(ReplyError):
        parse_list_reply(_raw(reply))


def test_duplicate_keys_and_oversize_are_not_a_list():
    with pytest.raises(ReplyError):
        parse_list_reply(b'{"v":1,"status":"ok","error":null,"projects":[],"projects":["a"]}')
    with pytest.raises(ReplyError):
        parse_list_reply(b" " * (remote_adapter.MAX_MESSAGE_BYTES + 1))


def test_a_refusal_in_the_turn_shape_says_its_code():
    with pytest.raises(ListRefused) as refused:
        parse_list_reply(_raw(ERROR_REPLY))
    assert refused.value.code == "disabled"


class Lister:
    name = "full"

    def __init__(self, answer):
        self.answer = answer

    def list_projects(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def run_turn(self, **kwargs):  # pragma: no cover - not used here
        raise AssertionError


def _full(remote):
    return FullMode(remote=remote, readonly=None, confirm_yes=("yes",), confirm_no=("no",),
                    min_speech_s=0.3)


def test_full_mode_lists_the_agent_hosts_projects():
    assert _full(Lister(["a", "b"])).project_names(lambda: ["pod"]) == (["a", "b"], "agent_host")


@pytest.mark.parametrize("failure", [
    AgentUnavailable(AgentUnavailable.UNREACHABLE),
    AgentUnavailable(AgentUnavailable.DISABLED),
    AgentTurnFailed("bad_request"),
    AgentTurnFailed(AgentTurnFailed.PROTOCOL),
    AgentTurnFailed(AgentTurnFailed.HOST_KEY),
])
def test_full_mode_lists_the_bridges_own_when_the_agent_host_cannot(failure):
    assert _full(Lister(failure)).project_names(lambda: ["pod"]) == (["pod"], "bridge")


def test_every_no_copy_line_passes_the_spoken_filter():
    from bridge.summary import _SECRET_LIKE, _UNSPEAKABLE
    assert set(full_mode.NO_COPY_LINES) == AgentUnavailable.REASONS
    for line in full_mode.NO_COPY_LINES.values():
        assert not _UNSPEAKABLE.search(line) and not _SECRET_LIKE.search(line), line


def _config(mode, tmp_path):
    from bridge.config import BridgeConfig, ProjectConfig
    return BridgeConfig(
        bind_host="127.0.0.1", port=0, token="t",
        projects={"demo": ProjectConfig("demo", tmp_path)}, allowed_tools=("Read",),
        session_store_path=tmp_path / "s.json", log_path=tmp_path / "b.log",
        whisper_model="base", kokoro_voice="v", claude_timeout_s=30, agent_mode=mode)


def test_readonly_mode_still_refuses_a_project_it_does_not_have(tmp_path):
    from bridge.config import ConfigError
    config = _config("readonly", tmp_path)
    assert config.project_for_turn("demo").working_dir == tmp_path
    with pytest.raises(ConfigError):
        config.project_for_turn("birdsong")


def test_full_mode_takes_any_name_of_the_agent_hosts_shape(tmp_path):
    from bridge.config import ConfigError
    config = _config("full", tmp_path)
    assert config.project_for_turn("demo").working_dir == tmp_path      # the pod's copy
    assert config.project_for_turn("birdsong").working_dir is None      # only there
    for name in ("..", ".x", "-x", "a b", "a/b", "x" * 65, ""):
        with pytest.raises(ConfigError):
            config.project_for_turn(name)
