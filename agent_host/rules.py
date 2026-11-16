"""The gate's rules, loaded from data (1.7, D36).

The base is the repository's `gate_rules.toml`, next to this module. Every
`*.toml` in the host-local rules directory (`rules_dir` in the
configuration) is merged on top, in name order: lists are joined, tables are
merged, `[[actions]]` and `[[option_classes]]` are appended. That directory
is where a user's own commands live; the repository must not know them.

Loading is strict. A file that cannot be read, is not TOML, or holds a value
of the wrong type raises `RulesError`, and the gate denies the call (D36,
"failing closed"): a half-understood rules file must never be read as "no
rule matched".
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

BASE_RULES_PATH = Path(__file__).resolve().parent / "gate_rules.toml"

# The classes a rule may put a call in (D36's table). `self` is not here: no
# rule assigns it, the protected paths do (agent_host.classify).
ACTION_CLASSES = frozenset({
    "merge", "deploy", "delete", "force", "api", "account", "message",
    "create", "outward", "root",
})


class RulesError(RuntimeError):
    """The rules cannot be used. The gate denies."""


@dataclass(frozen=True)
class Program:
    name: str
    groups: frozenset[str]
    read_verbs: frozenset[str]
    read_prefixes: tuple[str, ...]
    value_options: frozenset[str]
    flag_options: frozenset[str]
    skip_words: int


@dataclass(frozen=True)
class Action:
    cls: str
    programs: frozenset[str]
    paths: tuple[tuple[str, ...], ...]
    verbs: frozenset[str]

    def matches(self, program: str, subpath: tuple[str, ...], verb: str | None = None) -> bool:
        """`subpath` is the call's words up to and including its verb;
        `verb` is that verb, the last of them unless the caller says so."""
        if program not in self.programs:
            return False
        if not self.paths and not self.verbs:
            return True
        if any(subpath[:len(path)] == path for path in self.paths):
            return True
        if verb is None:
            verb = subpath[-1] if subpath else None
        return verb is not None and verb in self.verbs


@dataclass(frozen=True)
class OptionClass:
    cls: str
    programs: frozenset[str]
    options: frozenset[str]


@dataclass(frozen=True)
class HttpClient:
    name: str
    short_letters: frozenset[str]
    long_options: tuple[str, ...]
    httpie: bool


@dataclass(frozen=True)
class GhRules:
    own_repo_paths: frozenset[tuple[str, ...]]
    positional_repo_paths: frozenset[tuple[str, ...]]
    repo_options: frozenset[str]
    foreign_message_paths: frozenset[tuple[str, ...]]
    api_method_options: frozenset[str]
    api_field_options: frozenset[str]
    api_short_letters: frozenset[str]


@dataclass(frozen=True)
class Rules:
    programs: dict[str, Program]
    actions: tuple[Action, ...]
    option_classes: tuple[OptionClass, ...]
    http: dict[str, HttpClient]
    gh: GhRules
    default_branches: frozenset[str]
    transfer_programs: frozenset[str]
    transfer_host_only: frozenset[str]
    root_programs: frozenset[str]
    shells: frozenset[str]
    indirect: frozenset[str]
    harmless: frozenset[str]
    harmless_git: frozenset[str]
    interpreters: tuple[str, ...]
    inline_letters: frozenset[str]
    inline_words: frozenset[str]
    tools_ungated: frozenset[str]
    tools_write: frozenset[str]
    temp_roots: tuple[str, ...]
    mcp_words: tuple[str, ...]
    self_home_paths: tuple[str, ...]
    self_claude_dir_paths: tuple[str, ...]
    self_read_programs: frozenset[str]
    self_tree_programs: frozenset[str]

    @property
    def action_programs(self) -> frozenset[str]:
        out: set[str] = set()
        for action in self.actions:
            out |= action.programs
        return frozenset(out)

    @property
    def known_programs(self) -> frozenset[str]:
        """Every program some rule can gate. In a compound command these are
        the words that start a candidate command (agent_host.shell)."""
        return (
            frozenset(self.programs) | self.action_programs | frozenset(self.http)
            | self.transfer_programs | self.root_programs | {"git", "rm", "find"}
        )


# --- merging -----------------------------------------------------------------

def _merge(base: dict, extra: dict, where: str) -> dict:
    out = dict(base)
    for key, value in extra.items():
        if key not in out:
            out[key] = value
        elif isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value, f"{where}.{key}")
        elif isinstance(out[key], list) and isinstance(value, list):
            out[key] = out[key] + [item for item in value if item not in out[key]]
        elif type(out[key]) is type(value):
            out[key] = value
        else:
            raise RulesError(f"{where}.{key}: cannot merge {type(value).__name__} "
                             f"into {type(out[key]).__name__}")
    return out


def _read_toml(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RulesError(f"cannot read rules file {path}: {exc}") from exc
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise RulesError(f"rules file {path} is not valid TOML: {exc}") from exc


# --- strict readers ------------------------------------------------------------

def _table(data: dict, key: str, where: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise RulesError(f"{where}{key} must be a table")
    return value


def _strings(data: dict, key: str, where: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        raise RulesError(f"{where}{key} must be a list of non-empty strings")
    return tuple(value)


def _paths(data: dict, key: str, where: str) -> tuple[tuple[str, ...], ...]:
    value = data.get(key, [])
    ok = isinstance(value, list) and all(
        isinstance(path, list) and path and all(isinstance(x, str) and x for x in path)
        for path in value
    )
    if not ok:
        raise RulesError(f"{where}{key} must be a list of non-empty lists of strings")
    return tuple(tuple(path) for path in value)


def _known_keys(data: dict, allowed: set[str], where: str) -> None:
    extra = set(data) - allowed
    if extra:
        raise RulesError(f"{where}: unknown key(s) {sorted(extra)}")


def _program(name: str, data: object, read_verbs: tuple[str, ...],
             global_flags: tuple[str, ...]) -> Program:
    where = f"programs.{name}."
    if not isinstance(data, dict):
        raise RulesError(f"programs.{name} must be a table")
    _known_keys(data, {"groups", "read_verbs", "read_prefixes", "value_options",
                       "flag_options", "skip_words"}, f"programs.{name}")
    skip_words = data.get("skip_words", 0)
    if not isinstance(skip_words, int) or isinstance(skip_words, bool) or skip_words < 0:
        raise RulesError(f"{where}skip_words must be a non-negative integer")
    return Program(
        name=name,
        groups=frozenset(_strings(data, "groups", where)),
        read_verbs=frozenset(read_verbs) | frozenset(_strings(data, "read_verbs", where)),
        read_prefixes=_strings(data, "read_prefixes", where),
        value_options=frozenset(_strings(data, "value_options", where)),
        flag_options=frozenset(global_flags) | frozenset(_strings(data, "flag_options", where)),
        skip_words=skip_words,
    )


def _action(index: int, data: object) -> Action:
    where = f"actions[{index}]."
    if not isinstance(data, dict):
        raise RulesError(f"actions[{index}] must be a table")
    _known_keys(data, {"class", "programs", "paths", "verbs"}, f"actions[{index}]")
    cls = data.get("class")
    if cls not in ACTION_CLASSES:
        raise RulesError(f"{where}class must be one of {sorted(ACTION_CLASSES)}")
    programs = _strings(data, "programs", where)
    if not programs:
        raise RulesError(f"{where}programs must name at least one program")
    return Action(
        cls=cls,
        programs=frozenset(programs),
        paths=_paths(data, "paths", where),
        verbs=frozenset(_strings(data, "verbs", where)),
    )


def _option_class(index: int, data: object) -> OptionClass:
    where = f"option_classes[{index}]."
    if not isinstance(data, dict):
        raise RulesError(f"option_classes[{index}] must be a table")
    _known_keys(data, {"class", "programs", "options"}, f"option_classes[{index}]")
    cls = data.get("class")
    if cls not in ACTION_CLASSES:
        raise RulesError(f"{where}class must be one of {sorted(ACTION_CLASSES)}")
    programs = _strings(data, "programs", where)
    options = _strings(data, "options", where)
    if not programs or not options:
        raise RulesError(f"{where}programs and options must not be empty")
    return OptionClass(cls=cls, programs=frozenset(programs), options=frozenset(options))


def _http(name: str, data: object) -> HttpClient:
    where = f"http.{name}."
    if not isinstance(data, dict):
        raise RulesError(f"http.{name} must be a table")
    _known_keys(data, {"short_letters", "long_options", "httpie"}, f"http.{name}")
    httpie = data.get("httpie", False)
    if not isinstance(httpie, bool):
        raise RulesError(f"{where}httpie must be true or false")
    letters = _strings(data, "short_letters", where)
    if any(len(letter) != 1 for letter in letters):
        raise RulesError(f"{where}short_letters must be single letters")
    return HttpClient(
        name=name,
        short_letters=frozenset(letters),
        long_options=_strings(data, "long_options", where),
        httpie=httpie,
    )


def rules_from_dict(data: dict) -> Rules:
    _known_keys(data, {
        "version", "read_verbs", "global_flags", "programs", "gh", "http", "git",
        "transfer", "root", "shell", "rm", "mcp", "self", "actions", "option_classes",
        "interpreters", "tools",
    }, "rules")
    if data.get("version") != 1:
        raise RulesError("rules: version must be 1")

    read_verbs = _strings(data, "read_verbs", "")
    if not read_verbs:
        raise RulesError("read_verbs must not be empty")
    global_flags = _strings(data, "global_flags", "")

    programs_raw = _table(data, "programs", "")
    if not programs_raw:
        raise RulesError("programs must not be empty")
    programs = {
        name: _program(name, raw, read_verbs, global_flags)
        for name, raw in programs_raw.items()
    }

    actions_raw = data.get("actions", [])
    if not isinstance(actions_raw, list):
        raise RulesError("actions must be an array of tables")
    option_classes_raw = data.get("option_classes", [])
    if not isinstance(option_classes_raw, list):
        raise RulesError("option_classes must be an array of tables")

    gh = _table(data, "gh", "")
    _known_keys(gh, {"own_repo_paths", "positional_repo_paths", "repo_options",
                     "foreign_message_paths", "api_method_options",
                     "api_field_options", "api_short_letters"}, "gh")
    git = _table(data, "git", "")
    _known_keys(git, {"default_branches"}, "git")
    transfer = _table(data, "transfer", "")
    _known_keys(transfer, {"programs", "host_only"}, "transfer")
    root = _table(data, "root", "")
    _known_keys(root, {"programs"}, "root")
    shell = _table(data, "shell", "")
    _known_keys(shell, {"shells", "indirect", "harmless", "harmless_git"}, "shell")
    interpreters = _table(data, "interpreters", "")
    _known_keys(interpreters, {"programs", "inline_letters", "inline_words"}, "interpreters")
    tools = _table(data, "tools", "")
    _known_keys(tools, {"ungated", "write"}, "tools")
    rm = _table(data, "rm", "")
    _known_keys(rm, {"temp_roots"}, "rm")
    mcp = _table(data, "mcp", "")
    _known_keys(mcp, {"words"}, "mcp")
    self_ = _table(data, "self", "")
    _known_keys(self_, {"home_paths", "claude_dir_paths", "read_programs",
                        "tree_programs"}, "self")

    root_programs = _strings(root, "programs", "root.")
    if not root_programs:
        raise RulesError("root.programs must not be empty")
    mcp_words = _strings(mcp, "words", "mcp.")
    if not mcp_words:
        raise RulesError("mcp.words must not be empty")

    return Rules(
        programs=programs,
        actions=tuple(_action(i, raw) for i, raw in enumerate(actions_raw)),
        option_classes=tuple(_option_class(i, raw) for i, raw in enumerate(option_classes_raw)),
        http={name: _http(name, raw) for name, raw in _table(data, "http", "").items()},
        gh=GhRules(
            own_repo_paths=frozenset(_paths(gh, "own_repo_paths", "gh.")),
            positional_repo_paths=frozenset(_paths(gh, "positional_repo_paths", "gh.")),
            repo_options=frozenset(_strings(gh, "repo_options", "gh.")),
            foreign_message_paths=frozenset(_paths(gh, "foreign_message_paths", "gh.")),
            api_method_options=frozenset(_strings(gh, "api_method_options", "gh.")),
            api_field_options=frozenset(_strings(gh, "api_field_options", "gh.")),
            api_short_letters=frozenset(_strings(gh, "api_short_letters", "gh.")),
        ),
        default_branches=frozenset(_strings(git, "default_branches", "git.")),
        transfer_programs=frozenset(_strings(transfer, "programs", "transfer.")),
        transfer_host_only=frozenset(_strings(transfer, "host_only", "transfer.")),
        root_programs=frozenset(root_programs),
        shells=frozenset(_strings(shell, "shells", "shell.")),
        indirect=frozenset(_strings(shell, "indirect", "shell.")),
        harmless=frozenset(_strings(shell, "harmless", "shell.")),
        harmless_git=frozenset(_strings(shell, "harmless_git", "shell.")),
        interpreters=_strings(interpreters, "programs", "interpreters."),
        inline_letters=frozenset(_strings(interpreters, "inline_letters", "interpreters.")),
        inline_words=frozenset(_strings(interpreters, "inline_words", "interpreters.")),
        tools_ungated=frozenset(_strings(tools, "ungated", "tools.")),
        tools_write=frozenset(_strings(tools, "write", "tools.")),
        temp_roots=_strings(rm, "temp_roots", "rm."),
        mcp_words=tuple(word.lower() for word in mcp_words),
        self_home_paths=_strings(self_, "home_paths", "self."),
        self_claude_dir_paths=_strings(self_, "claude_dir_paths", "self."),
        self_read_programs=frozenset(_strings(self_, "read_programs", "self.")),
        self_tree_programs=frozenset(_strings(self_, "tree_programs", "self.")),
    )


def load_rules(rules_dir: Path | None, base_path: Path = BASE_RULES_PATH) -> Rules:
    """The repository's rules with the host-local ones merged on top.

    A missing rules directory is the normal state of a host without own
    rules. A directory that exists and cannot be listed, or any file in it
    that cannot be used, is an error.
    """
    data = _read_toml(base_path)
    if rules_dir is not None:
        try:
            extra_files = sorted(p for p in rules_dir.iterdir() if p.name.endswith(".toml"))
        except FileNotFoundError:
            extra_files = []
        except OSError as exc:
            raise RulesError(f"cannot list rules directory {rules_dir}: {exc}") from exc
        for path in extra_files:
            extra = _read_toml(path)
            if extra.get("version", 1) != 1:
                raise RulesError(f"rules file {path}: version must be 1")
            data = _merge(data, extra, path.name)
    return rules_from_dict(data)
