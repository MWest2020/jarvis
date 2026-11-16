"""Parsing one version-1 turn request (1.1, D33).

The wrapper reads at most MAX_REQUEST_BYTES from stdin and refuses anything
that is not exactly the closed shape: `{"v":1,"project":...,"prompt":...,
"confirm":null}` or with a confirm object. An unknown field, a wrong type, or
a confirm paired with a non-empty prompt is `bad_request`.

One other request exists (jarvis-general-conversation D51): exactly
`{"v":1,"list":true}`, asking for the names of the projects a turn would
accept. It is an extension of version 1, not a version 2: an older wrapper
reads `list` as an unknown field and answers `bad_request`. Nothing here
decides whether the project or the confirm are *valid* turns to run -
project resolution (agent_host.projects) and the gate (phase 1b) do that.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

MAX_REQUEST_BYTES = 64 * 1024

_ALLOWED_FIELDS = frozenset({"v", "project", "prompt", "confirm"})
_LIST_FIELDS = frozenset({"v", "list"})
_ALLOWED_ANSWERS = frozenset({"yes", "no"})
_CONFIRM_FIELDS = frozenset({"id", "sha256", "answer"})


class RequestError(ValueError):
    """Raised for a request that is `bad_request` (1.1, D33)."""


@dataclass(frozen=True)
class Confirm:
    id: str
    sha256: str
    answer: str


@dataclass(frozen=True)
class Request:
    project: str
    prompt: str
    confirm: Confirm | None


@dataclass(frozen=True)
class ListRequest:
    """`{"v":1,"list":true}`: the names of the projects, nothing else (D51)."""


def parse_request(raw: bytes) -> Request | ListRequest:
    if len(raw) > MAX_REQUEST_BYTES:
        raise RequestError(f"request body exceeds {MAX_REQUEST_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestError(f"request body is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RequestError("request body must be a JSON object")
    if "list" in data:
        return _parse_list(data)
    extra = set(data) - _ALLOWED_FIELDS
    if extra:
        raise RequestError(f"unknown field(s): {sorted(extra)}")
    missing = _ALLOWED_FIELDS - set(data)
    if missing:
        raise RequestError(f"missing field(s): {sorted(missing)}")

    if data.get("v") != 1:
        raise RequestError("v must be 1")

    project = data["project"]
    if not isinstance(project, str) or not project:
        raise RequestError("project must be a non-empty string")

    prompt = data["prompt"]
    if not isinstance(prompt, str):
        raise RequestError("prompt must be a string")

    confirm_raw = data["confirm"]
    confirm = None
    if confirm_raw is not None:
        confirm = _parse_confirm(confirm_raw)
        if prompt != "":
            raise RequestError("a request with confirm must carry an empty prompt")

    return Request(project=project, prompt=prompt, confirm=confirm)


def _parse_confirm(confirm_raw: object) -> Confirm:
    if not isinstance(confirm_raw, dict):
        raise RequestError("confirm must be an object or null")
    extra = set(confirm_raw) - _CONFIRM_FIELDS
    if extra:
        raise RequestError(f"unknown confirm field(s): {sorted(extra)}")
    missing = _CONFIRM_FIELDS - set(confirm_raw)
    if missing:
        raise RequestError(f"missing confirm field(s): {sorted(missing)}")
    id_ = confirm_raw["id"]
    sha256 = confirm_raw["sha256"]
    answer = confirm_raw["answer"]
    if not isinstance(id_, str) or not id_:
        raise RequestError("confirm.id must be a non-empty string")
    if not isinstance(sha256, str) or not sha256:
        raise RequestError("confirm.sha256 must be a non-empty string")
    if answer not in _ALLOWED_ANSWERS:
        raise RequestError("confirm.answer must be 'yes' or 'no'")
    return Confirm(id=id_, sha256=sha256, answer=answer)


def _parse_list(data: dict) -> ListRequest:
    if set(data) != _LIST_FIELDS:
        odd = sorted(set(data) ^ _LIST_FIELDS)
        raise RequestError(f"a list request has exactly v and list, not {odd}")
    if type(data["v"]) is not int or data["v"] != 1:
        raise RequestError("v must be 1")
    if data["list"] is not True:
        raise RequestError("list must be true")
    return ListRequest()
