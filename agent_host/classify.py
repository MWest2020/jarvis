"""From a tool call to its classes (1.7, 1.7b, D36).

`classify()` answers three things about one call, and nothing else:

- is it gated at all (a rule by default or a rule that names an action
  matched);
- which classes it is in (merge, deploy, delete, force, api, account,
  message, create, outward, root, self). A gated call can be in no class:
  the default rules decide *that* a call is gated, the naming rules decide
  whether it can ever be read back;
- must it be run alone (a gated command inside a compound `Bash` string).

Phase 1b1 stops there. The gate (`agent_host.decision`) denies every gated
call; nothing is pending and nothing is confirmable until 1b2 adds naming,
the hash and the grant on top of this same function. The wrapper will call
this module too, so that what is read back and what is released can never
be judged by two sets of rules.

The class `self` is Jarvis's own rules: the gate, the wrapper, their rules,
settings and configuration, `authorized_keys`, Claude Code's user and
managed settings, the user's instructions, agents, skills and plugins, the
shell start-up files, every file a configured hook runs, and any checkout's
`.claude/settings*.json`. A write to one of them, by `Edit`, `Write` or a
shell command, is always desk-only and never pending.

Looking at the surroundings is allowed here and only here: *that* a bare
`git push` is gated depends on where it would land. Naming a target from
the surroundings is never allowed (1b2).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent_host import shell
from agent_host.checkout_settings import git_top_level
from agent_host.config import AgentHostConfig
from agent_host.rules import Program, Rules

_REPO_RE = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
_REMOTE_NAME_RE = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]*")
_REMOTE_SPEC_RE = re.compile(r"(?:[^@/:\s]+@)?(\[[0-9A-Fa-f:.]+\]|[^/:\s\[\]]+):")
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_GLOB_RE = re.compile(r"[*?\[]")
_HTTP_METHOD_RE = re.compile(r"[A-Za-z]+")
_SAFE_HTTP_METHODS = frozenset({"GET", "HEAD"})

_WRITE_TOOLS = {
    "Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}

_GIT_VALUE_OPTIONS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--config-env", "--super-prefix",
})
_GIT_FLAGS = frozenset({
    "--no-pager", "-p", "-P", "--paginate", "--bare", "--no-replace-objects",
    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs", "--no-optional-locks", "--version", "--help", "-h",
})
# Options of git itself that change which repository or configuration a
# push uses: with one of them, nothing about the push is resolved here.
_GIT_REDIRECTING = ("-C", "-c", "--git-dir", "--work-tree", "--config-env", "--namespace")
_PUSH_VALUE_OPTIONS = ("--repo", "--receive-pack", "--exec", "--push-option")


@dataclass(frozen=True)
class Verdict:
    gated: bool = False
    classes: tuple[str, ...] = ()
    run_alone: bool = False
    # Which rule decided, for tests, the record and a reader of a denial.
    rule: str = ""
    # For `Bash`: the innermost command and what it was wrapped in.
    command: shell.Command | None = None

    @property
    def desk_only(self) -> bool:
        return "self" in self.classes


UNGATED = Verdict()


GitRunner = Callable[[list[str], Path], "str | None"]


def run_git(args: list[str], cwd: Path) -> str | None:
    """`git <args>` in `cwd`: its output, or None when it fails for any reason."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


# --- the protected paths of the class `self` ------------------------------------

@dataclass(frozen=True)
class Protected:
    """Real paths that are Jarvis's own rules. A directory covers its tree."""
    paths: frozenset[str]

    def covers(self, path: str | os.PathLike) -> bool:
        real = os.path.realpath(path)
        if _is_claude_settings(real):
            return True
        return any(real == p or real.startswith(p + os.sep) for p in self.paths)

    def holds(self, path: str | os.PathLike) -> bool:
        """`path` is a directory with a protected path somewhere inside it."""
        real = os.path.realpath(path)
        prefix = real.rstrip(os.sep) + os.sep
        return any(p.startswith(prefix) for p in self.paths)

    def parent_of_one(self, path: str | os.PathLike) -> bool:
        real = os.path.realpath(path)
        if os.path.basename(real) == ".claude":
            return True
        return any(os.path.dirname(p) == real for p in self.paths)


def _is_claude_settings(real: str) -> bool:
    """Any checkout's `.claude/settings*.json`, and the user's own."""
    return (
        os.path.basename(os.path.dirname(real)) == ".claude"
        and fnmatch.fnmatchcase(os.path.basename(real), "settings*.json")
    )


def hook_files(settings_path: Path, home: Path) -> set[str]:
    """Every existing file a hook in this Claude Code settings file runs.

    The shape (`hooks.<event>[].hooks[].command`) is Claude Code's, not this
    repository's; a recorded sample fixes it in the tests. A file that is
    missing or cannot be parsed protects nothing here: the settings file
    itself stays protected whatever is in it.
    """
    try:
        data = json.loads(settings_path.read_text())
    except (OSError, ValueError):
        return set()
    found: set[str] = set()
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return found
    commands: list[str] = []
    for matchers in hooks.values():
        if not isinstance(matchers, list):
            continue
        for matcher in matchers:
            entries = matcher.get("hooks") if isinstance(matcher, dict) else None
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("command"), str):
                    commands.append(entry["command"])
    for command in commands:
        try:
            words = shlex.split(command)
        except ValueError:
            words = command.split()
        for word in words:
            expanded = _expand_home(word, home)
            if os.path.isabs(expanded) and os.path.isfile(expanded):
                found.add(os.path.realpath(expanded))
    return found


def protected_paths(
    config: AgentHostConfig, rules: Rules, *, home: Path, lib_dir: Path,
) -> Protected:
    claude_dir = config.user_settings_path.parent
    paths: set[Path] = {
        lib_dir,
        config.config_path,
        config.environment_file,
        config.rules_dir,
        config.turn_settings_path,
        config.prompt_path,
        config.user_settings_path,
        config.managed_settings_path,
    }
    if config.second_prompt_path is not None:
        paths.add(config.second_prompt_path)
    paths |= {home / rel for rel in rules.self_home_paths}
    paths |= {claude_dir / rel for rel in rules.self_claude_dir_paths}
    real = {os.path.realpath(p) for p in paths}
    for settings in (config.user_settings_path, claude_dir / "settings.local.json",
                     config.managed_settings_path, config.turn_settings_path):
        real |= hook_files(settings, home)
    return Protected(frozenset(real))


def _expand_home(word: str, home: Path) -> str:
    text = word.replace("${HOME}", str(home)).replace("$HOME", str(home))
    if text == "~":
        return str(home)
    if text.startswith("~/"):
        return str(home) + text[1:]
    return text


# --- the context of one call ----------------------------------------------------

@dataclass(frozen=True)
class Context:
    rules: Rules
    cwd: Path
    home: Path
    own_owners: frozenset[str]
    protected: Protected
    temp_roots: tuple[str, ...]
    git: GitRunner = field(default=run_git)


def context_for(
    config: AgentHostConfig, rules: Rules, *, cwd: Path, home: Path, lib_dir: Path,
    extra_temp_roots: tuple[str, ...] = (), git: GitRunner = run_git,
) -> Context:
    roots = tuple(rules.temp_roots) + tuple(extra_temp_roots)
    return Context(
        rules=rules,
        cwd=cwd,
        home=home,
        own_owners=frozenset(owner.lower() for owner in config.own_owners),
        protected=protected_paths(config, rules, home=home, lib_dir=lib_dir),
        temp_roots=tuple(os.path.realpath(root) for root in roots if root),
        git=git,
    )


# --- the entry point ------------------------------------------------------------

def classify(tool_name: str, tool_input: dict, ctx: Context) -> Verdict:
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            raise ValueError("a Bash call without a command string")
        return _classify_bash(command, ctx)

    if tool_name in _WRITE_TOOLS:
        path = tool_input.get(_WRITE_TOOLS[tool_name])
        if not isinstance(path, str) or not path:
            raise ValueError(f"a {tool_name} call without a path")
        if ctx.protected.covers(_resolve(path, ctx)):
            return Verdict(gated=True, classes=("self",), rule="self:write")
        return UNGATED

    if tool_name.startswith("mcp__"):
        name = tool_name.lower()
        for word in ctx.rules.mcp_words:
            if word in name:
                return Verdict(gated=True, rule=f"mcp:{word}")
        return UNGATED

    return UNGATED


def _resolve(path: str, ctx: Context) -> str:
    expanded = _expand_home(path, ctx.home)
    if not os.path.isabs(expanded):
        expanded = os.path.join(str(ctx.cwd), expanded)
    return os.path.normpath(expanded)


# --- Bash -----------------------------------------------------------------------

def _classify_bash(command: str, ctx: Context) -> Verdict:
    rules = ctx.rules
    if _shell_writes_protected(command, ctx):
        return Verdict(gated=True, classes=("self",), rule="self:shell")

    analysis = shell.analyse(
        command, known_programs=rules.known_programs, root_programs=rules.root_programs,
        shells=rules.shells, indirect=rules.indirect,
    )

    if analysis.compound:
        if analysis.root_word:
            return Verdict(gated=True, classes=("root",), run_alone=True, rule="root:any-position")
        for candidate in analysis.commands:
            verdict = _judge(candidate.argv, ctx)
            if verdict.gated:
                return Verdict(gated=True, classes=verdict.classes, run_alone=True,
                               rule=verdict.rule, command=candidate)
        return UNGATED

    if not analysis.commands:
        return UNGATED
    inner = analysis.commands[0]
    verdict = _judge(inner.argv, ctx)
    classes = list(verdict.classes)
    is_root = analysis.root_word or any(kind == "root" for kind, _ in inner.wrappers)
    if is_root and "root" not in classes:
        classes.insert(0, "root")
    if not verdict.gated and not is_root:
        return UNGATED
    return Verdict(gated=True, classes=tuple(classes), rule=verdict.rule or "root:any-position",
                   command=inner)


def _judge(argv: tuple[str, ...], ctx: Context) -> Verdict:
    """One command, already unwrapped: is it gated, and in which classes."""
    if not argv:
        return UNGATED
    rules = ctx.rules
    program = shell.program_name(argv[0])
    args = list(argv[1:])

    if program in rules.root_programs:
        return Verdict(gated=True, classes=("root",), rule=f"root:{program}")
    if program == "git":
        return _judge_git(args, ctx)
    if program == "rm":
        return _judge_rm(args, ctx)
    if program in rules.transfer_programs:
        return _judge_transfer(program, args, ctx)
    if program in rules.http:
        return _judge_http(program, args, ctx)
    if program in rules.programs:
        return _judge_default(rules.programs[program], args, ctx)
    classes = _action_classes(program, (), args, ctx)
    if classes:
        return Verdict(gated=True, classes=classes, rule=f"action:{program}")
    return UNGATED


def _action_classes(
    program: str, subpath: tuple[str, ...], args: list[str], ctx: Context,
) -> tuple[str, ...]:
    classes: list[str] = []
    for action in ctx.rules.actions:
        if action.matches(program, subpath) and action.cls not in classes:
            classes.append(action.cls)
    return tuple(classes)


def _option_classes(program: str, args: list[str], ctx: Context) -> tuple[str, ...]:
    classes: list[str] = []
    for rule in ctx.rules.option_classes:
        if program not in rule.programs or rule.cls in classes:
            continue
        if any(arg.split("=", 1)[0] in rule.options for arg in args):
            classes.append(rule.cls)
    return tuple(classes)


# --- rules by default -------------------------------------------------------------

def find_verb(program: Program, args: list[str]) -> tuple[tuple[str, ...], bool, list[str]]:
    """The words up to and including the verb, whether the verb could be
    placed, and what follows it.

    An option before the verb must be one the rules know, or carry its value
    with `=`. Anything else makes the verb impossible to place: with
    `kubectl -x get delete pod` nobody can say whether `get` is the verb or
    the value of `-x`, and such a call is gated.
    """
    path: list[str] = []
    skip = program.skip_words
    index = 0
    while index < len(args):
        word = args[index]
        if word.startswith("-") and len(word) > 1:
            name = word.split("=", 1)[0]
            if "=" in word:
                index += 1
            elif name in program.value_options:
                index += 2
            elif name in program.flag_options:
                index += 1
            else:
                return tuple(path), False, args[index:]
            continue
        path.append(word)
        index += 1
        if skip > 0:
            skip -= 1
            continue
        if word in program.groups:
            continue
        return tuple(path), True, args[index:]
    return (), True, []  # only groups and options: no verb, the program prints its help


def _is_read_verb(program: Program, verb: str) -> bool:
    return verb in program.read_verbs or any(verb.startswith(p) for p in program.read_prefixes)


def _judge_default(program: Program, args: list[str], ctx: Context) -> Verdict:
    subpath, placed, rest = find_verb(program, args)
    name = program.name
    if not placed:
        return Verdict(gated=True, classes=_option_classes(name, args, ctx),
                       rule=f"default:{name}:option-before-verb")
    if not subpath:
        return UNGATED

    if name == "gh":
        special = _judge_gh(subpath, args, rest, ctx)
        if special is not None:
            return special

    if _is_read_verb(program, subpath[-1]):
        return UNGATED

    classes = list(_action_classes(name, subpath, args, ctx))
    for cls in _option_classes(name, args, ctx):
        if cls not in classes:
            classes.append(cls)
    return Verdict(gated=True, classes=tuple(classes), rule=f"default:{name}:{' '.join(subpath)}")


# --- gh -----------------------------------------------------------------------------

def explicit_repo(args: list[str], repo_options: frozenset[str]) -> str | None:
    """The value of `--repo` / `-R` as written in the call, or None."""
    for index, word in enumerate(args):
        if word in repo_options:
            return args[index + 1] if index + 1 < len(args) else None
        for option in repo_options:
            if option.startswith("--") and word.startswith(option + "="):
                return word[len(option) + 1:]
            if not option.startswith("--") and word.startswith(option) and len(word) > len(option):
                return word[len(option):]
    return None


def is_own_repo(repo: str | None, own_owners: frozenset[str]) -> bool:
    if repo is None or not _REPO_RE.fullmatch(repo):
        return False
    return repo.split("/", 1)[0].lower() in own_owners


def _judge_gh(
    subpath: tuple[str, ...], args: list[str], rest: list[str], ctx: Context,
) -> Verdict | None:
    gh = ctx.rules.gh
    rule = f"default:gh:{' '.join(subpath)}"

    if subpath == ("api",):
        if _gh_api_writes(rest, ctx):
            return Verdict(gated=True, classes=("api",), rule="gh-api:write")
        return UNGATED

    if subpath in gh.own_repo_paths or subpath in gh.foreign_message_paths:
        if subpath in gh.positional_repo_paths:
            positional = [word for word in rest if not word.startswith("-")]
            repo = positional[0] if positional else None
        else:
            repo = explicit_repo(args, gh.repo_options)
        if subpath in gh.own_repo_paths and is_own_repo(repo, ctx.own_owners):
            return UNGATED
        classes: tuple[str, ...] = ()
        if subpath in gh.foreign_message_paths and not is_own_repo(repo, ctx.own_owners):
            classes = ("message",)
        return Verdict(gated=True, classes=classes, rule=rule)
    return None


def _gh_api_writes(rest: list[str], ctx: Context) -> bool:
    gh = ctx.rules.gh
    for index, word in enumerate(rest):
        name = word.split("=", 1)[0]
        if name in gh.api_field_options:
            return True
        if name in gh.api_method_options:
            if "=" in word:
                method = word.split("=", 1)[1]
            else:
                method = rest[index + 1] if index + 1 < len(rest) else ""
            if method.upper() != "GET":
                return True
            continue
        if word.startswith("-") and not word.startswith("--"):
            if word[:2] == "-X" and len(word) > 2 and "=" not in word:
                if word[2:].upper() != "GET":
                    return True
                continue
            if any(letter in gh.api_short_letters for letter in word[1:].split("=", 1)[0]):
                return True
    return False


# --- HTTP clients ---------------------------------------------------------------------

def _long_option_matches(name: str, options: tuple[str, ...]) -> bool:
    # `--data-urlencode` starts with `--data`; `--post-d` abbreviates
    # `--post-data` (GNU long options may be shortened).
    return any(
        name.startswith(option) or (len(name) > 2 and option.startswith(name))
        for option in options
    )


def _judge_http(program: str, args: list[str], ctx: Context) -> Verdict:
    client = ctx.rules.http[program]
    gated = Verdict(gated=True, classes=("api",), rule=f"http:{program}")
    positional: list[str] = []
    for word in args:
        if word.startswith("--") and len(word) > 2:
            if _long_option_matches(word.split("=", 1)[0], client.long_options):
                return gated
        elif word.startswith("-") and len(word) > 1:
            if any(letter in client.short_letters for letter in word[1:]):
                return gated
        else:
            positional.append(word)
    if client.httpie:
        # httpie reads a first argument of letters only as the method, in
        # any case, when a URL follows it.
        if len(positional) >= 2 and _HTTP_METHOD_RE.fullmatch(positional[0]):
            if positional[0].upper() not in _SAFE_HTTP_METHODS:
                return gated
            positional = positional[1:]
        for item in positional[1:]:
            # `name==value` is a query parameter and `Name:value` a header;
            # every other item is data, and data makes the request a POST.
            if "==" in item:
                continue
            if ":" in item and "=" not in item and "@" not in item:
                continue
            return gated
    return UNGATED


# --- scp, rsync, sftp --------------------------------------------------------------------

def remote_hosts(program: str, args: list[str], ctx: Context) -> list[str]:
    """The other hosts a copy command names, as written."""
    hosts: list[str] = []
    for word in args:
        if word.startswith("-"):
            continue
        if _URL_RE.match(word):
            hosts.append(word)
            continue
        match = _REMOTE_SPEC_RE.match(word)
        if match:
            hosts.append(match.group(1))
        elif program in ctx.rules.transfer_host_only:
            hosts.append(word)
    return hosts


def _judge_transfer(program: str, args: list[str], ctx: Context) -> Verdict:
    if remote_hosts(program, args, ctx):
        return Verdict(gated=True, classes=("outward",), rule=f"transfer:{program}")
    return UNGATED  # both ends local: the path rule of `self` has already looked


# --- rm -r -------------------------------------------------------------------------------

def _judge_rm(args: list[str], ctx: Context) -> Verdict:
    recursive = False
    targets: list[str] = []
    options_done = False
    for word in args:
        if options_done or not word.startswith("-") or word == "-":
            targets.append(word)
        elif word == "--":
            options_done = True
        elif word.startswith("--"):
            if len(word) > 3 and "--recursive".startswith(word):
                recursive = True
        elif "r" in word or "R" in word:
            recursive = True
    if not recursive:
        return UNGATED
    allowed = list(ctx.temp_roots)
    top = git_top_level(ctx.cwd)
    if top is not None:
        allowed.append(os.path.realpath(top))
    gated = Verdict(gated=True, classes=("delete",), rule="rm:recursive-outside")
    for target in targets:
        if "$" in target or "`" in target:
            return gated
        glob = _GLOB_RE.search(target)
        if glob:
            literal = target[:glob.start()]
            target = literal if literal.endswith("/") else os.path.dirname(literal)
            if not target:
                target = "."
        real = os.path.realpath(_resolve(target, ctx))
        if not any(real == root or real.startswith(root + os.sep) for root in allowed):
            return gated
    return UNGATED


# --- git push ---------------------------------------------------------------------------

def _git_subcommand(args: list[str]) -> tuple[str | None, list[str], bool, bool]:
    """(subcommand, its arguments, placed, redirected)."""
    redirected = False
    index = 0
    while index < len(args):
        word = args[index]
        if not word.startswith("-"):
            return word, args[index + 1:], True, redirected
        name = word.split("=", 1)[0]
        if name in _GIT_REDIRECTING:
            redirected = True
        if "=" in word and name.startswith("--"):
            index += 1
        elif name in _GIT_VALUE_OPTIONS:
            index += 2
        elif name in _GIT_FLAGS:
            index += 1
        else:
            return None, args[index:], False, redirected
    return None, [], True, redirected


def _long_is(name: str, full: str) -> bool:
    """`name` is `full` or an abbreviation of it (git accepts unique prefixes)."""
    return name == full or (len(name) > 2 and full.startswith(name))


@dataclass(frozen=True)
class Push:
    remote: str | None
    refspecs: tuple[str, ...]
    delete: bool
    force: bool
    wide: bool  # --all, --tags, --mirror, --branches
    repo_option: bool  # --repo: another remote than the one the branch names


def parse_push(args: list[str]) -> Push:
    """`git push` by git's own option grammar, never by single spellings."""
    delete = force = wide = repo_option = False
    positional: list[str] = []
    index = 0
    options_done = False
    while index < len(args):
        word = args[index]
        index += 1
        if options_done or not word.startswith("-") or word == "-":
            positional.append(word)
            continue
        if word == "--":
            options_done = True
            continue
        if word.startswith("--"):
            name = word.split("=", 1)[0]
            if name.startswith("--no-"):
                continue
            if _long_is(name, "--delete") or _long_is(name, "--prune"):
                delete = True
            if name.startswith("--force") or _long_is(name, "--force") \
                    or _long_is(name, "--force-with-lease") or _long_is(name, "--force-if-includes"):
                force = True
            if _long_is(name, "--mirror"):
                force = wide = True
            if _long_is(name, "--all") or _long_is(name, "--tags") or _long_is(name, "--branches"):
                wide = True
            if _long_is(name, "--repo"):
                repo_option = True
            if "=" not in word and any(_long_is(name, option) for option in _PUSH_VALUE_OPTIONS):
                index += 1
            continue
        letters = word[1:]
        if "f" in letters:
            force = True
        if "d" in letters:
            delete = True
        if letters.endswith("o"):
            index += 1  # -o <push-option>
    remote = positional[0] if positional else None
    refspecs = tuple(positional[1:])
    for refspec in refspecs:
        if refspec.startswith("+"):
            force = True
        if refspec.lstrip("+").startswith(":"):
            delete = True
    return Push(remote=remote, refspecs=refspecs, delete=delete, force=force, wide=wide,
                repo_option=repo_option)


def push_destinations(push: Push) -> list[str] | None:
    """Where each refspec lands, as written; None when one cannot be read."""
    out: list[str] = []
    refspecs = list(push.refspecs)
    index = 0
    while index < len(refspecs):
        refspec = refspecs[index].lstrip("+")
        index += 1
        if refspec == "tag":
            if index >= len(refspecs):
                return None
            out.append("refs/tags/" + refspecs[index])
            index += 1
            continue
        if ":" in refspec:
            source, destination = refspec.split(":", 1)
            if not destination:
                return None
            if source:
                out.append(destination)
        else:
            out.append(refspec)
    return out


def _judge_git(args: list[str], ctx: Context) -> Verdict:
    subcommand, rest, placed, redirected = _git_subcommand(args)
    if not placed:
        if "push" in args:
            return Verdict(gated=True, rule="push:option-before-subcommand")
        return UNGATED
    if subcommand != "push":
        return UNGATED

    push = parse_push(rest)
    classes: list[str] = []
    if push.delete:
        classes.append("delete")
    if push.force:
        classes.append("force")
    if push.wide:
        return Verdict(gated=True, classes=tuple(classes), rule="push:wide")

    remote = push.remote
    if not push.refspecs:
        landing = None if redirected or push.repo_option else _bare_push_landing(remote, ctx)
        if landing is None:
            return Verdict(gated=True, classes=tuple(classes), rule="push:bare-unresolved")
        remote, branch = landing
        destinations = [branch]
    else:
        destinations = push_destinations(push)
        if destinations is None:
            return Verdict(gated=True, classes=tuple(classes), rule="push:refspec-unreadable")

    defaults = set(ctx.rules.default_branches)
    if not redirected and remote is not None and _REMOTE_NAME_RE.fullmatch(remote):
        head = ctx.git(["symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"], ctx.cwd)
        if head and head.startswith(remote + "/"):
            defaults.add(head[len(remote) + 1:])

    for destination in destinations:
        if destination in ("HEAD", "@"):
            current = None if redirected else ctx.git(["symbolic-ref", "--short", "HEAD"], ctx.cwd)
            if not current:
                return Verdict(gated=True, classes=tuple(classes), rule="push:head-unresolved")
            destination = current
        if destination.startswith("refs/heads/"):
            destination = destination[len("refs/heads/"):]
        if destination in defaults:
            classes.insert(0, "merge")
            break

    if classes:
        return Verdict(gated=True, classes=tuple(classes), rule="push:" + "+".join(classes))
    return UNGATED


def _bare_push_landing(remote: str | None, ctx: Context) -> tuple[str, str] | None:
    """(remote, branch) a `git push` without a refspec goes to, or None.

    Only the plain case is resolved: one branch, to the place `@{push}`
    names. Anything else (`push.default = matching`, a `remote.<name>.push`
    or `mirror` setting, no upstream, another remote than the branch's own)
    is not guessed at, and the push is gated.
    """
    git = ctx.git
    if (git(["config", "--get", "push.default"], ctx.cwd) or "") == "matching":
        return None
    full = git(["rev-parse", "--symbolic-full-name", "@{push}"], ctx.cwd)
    remotes = git(["remote"], ctx.cwd)
    if not full or not remotes:
        return None
    matches = [
        name for name in remotes.splitlines()
        if name and full.startswith(f"refs/remotes/{name}/")
    ]
    if len(matches) != 1:
        return None
    found = matches[0]
    if remote is not None and remote != found:
        return None
    if not _REMOTE_NAME_RE.fullmatch(found):
        return None
    if git(["config", "--get-all", f"remote.{found}.push"], ctx.cwd):
        return None
    if (git(["config", "--get", f"remote.{found}.mirror"], ctx.cwd) or "false") != "false":
        return None
    return found, full[len(f"refs/remotes/{found}/"):]


# --- shell writes to the class `self` -------------------------------------------------------

def _shell_writes_protected(command: str, ctx: Context) -> bool:
    """A shell command that writes, or may write, to one of Jarvis's own rules.

    The command is not parsed for what it does. If it names a protected
    path, it is a write unless every program in it is on the short list of
    programs that only read and nothing is redirected or substituted.
    """
    rules = ctx.rules
    text = command.replace("${HOME}", str(ctx.home)).replace("$HOME", str(ctx.home))
    segments = shell.lenient_segments(text)

    mentioned = False
    for words in segments:
        stripped, _ = shell.strip_prefixes(words)
        program = shell.program_name(stripped[0]) if stripped else ""
        for word in words:
            for part in re.split(r"[=,]", word):
                if not part or part.startswith("-"):
                    continue
                glob = _GLOB_RE.search(part)
                if glob:
                    literal = part[:glob.start()]
                    directory = literal if literal.endswith("/") else os.path.dirname(literal)
                    resolved = _resolve(directory or ".", ctx)
                    if ctx.protected.covers(resolved) or ctx.protected.parent_of_one(resolved):
                        mentioned = True
                    continue
                resolved = _resolve(part, ctx)
                if ctx.protected.covers(resolved):
                    mentioned = True
                elif program in rules.self_tree_programs and ctx.protected.holds(resolved):
                    return True
    if not mentioned:
        return False

    if any(mark in command for mark in (">", "$(", "`", "<(")):
        return True
    for words in segments:
        stripped, ok = shell.strip_prefixes(words)
        if not ok:
            return True
        if stripped and shell.program_name(stripped[0]) not in rules.self_read_programs:
            return True
    return False
