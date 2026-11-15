import json

import pytest

from agent_host.request import RequestError, parse_request


def _body(**overrides):
    data = {"v": 1, "project": "demo", "prompt": "what is 2+2", "confirm": None}
    data.update(overrides)
    return json.dumps(data).encode()


def test_parses_a_minimal_request():
    req = parse_request(_body())
    assert req.project == "demo"
    assert req.prompt == "what is 2+2"
    assert req.confirm is None


def test_parses_a_confirm():
    req = parse_request(_body(prompt="", confirm={"id": "a", "sha256": "b", "answer": "yes"}))
    assert req.confirm.id == "a"
    assert req.confirm.sha256 == "b"
    assert req.confirm.answer == "yes"


def test_an_extra_field_is_refused():
    with pytest.raises(RequestError):
        parse_request(_body(extra="nope"))


def test_a_missing_field_is_refused():
    raw = json.dumps({"v": 1, "project": "demo", "prompt": "hi"}).encode()
    with pytest.raises(RequestError):
        parse_request(raw)


def test_wrong_version_is_refused():
    with pytest.raises(RequestError):
        parse_request(_body(v=2))


def test_a_confirm_with_a_non_empty_prompt_is_refused():
    with pytest.raises(RequestError):
        parse_request(_body(prompt="not empty", confirm={"id": "a", "sha256": "b", "answer": "yes"}))


def test_a_confirm_with_an_unknown_answer_is_refused():
    with pytest.raises(RequestError):
        parse_request(_body(prompt="", confirm={"id": "a", "sha256": "b", "answer": "maybe"}))


def test_a_confirm_with_an_extra_field_is_refused():
    with pytest.raises(RequestError):
        parse_request(_body(prompt="", confirm={"id": "a", "sha256": "b", "answer": "yes", "x": 1}))


def test_a_confirm_missing_a_field_is_refused():
    with pytest.raises(RequestError):
        parse_request(_body(prompt="", confirm={"id": "a", "answer": "yes"}))


def test_non_json_is_refused():
    with pytest.raises(RequestError):
        parse_request(b"not json")


def test_a_json_array_is_refused():
    with pytest.raises(RequestError):
        parse_request(b"[1,2,3]")


def test_wrong_types_are_refused():
    with pytest.raises(RequestError):
        parse_request(_body(project=123))
    with pytest.raises(RequestError):
        parse_request(_body(prompt=123))


def test_an_oversized_body_is_refused():
    huge = json.dumps({"v": 1, "project": "demo", "prompt": "x" * 100_000, "confirm": None}).encode()
    with pytest.raises(RequestError):
        parse_request(huge)
