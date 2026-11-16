"""From a tool call to its classes (1.7, 1.7b, D36).

`classify()` answers three things about one call, and nothing else:

- is it gated at all (a rule by default or a rule that names an action
  matched);
- which classes it is in (merge, deploy, delete, force, api, account,
  message, create, outward, root, self). A gated call can be in no class:
  the default rules decide *that* a call is gated, the naming rules decide
  whether it can ever be read back;
- must it be run alone (a gated command inside a compound `Bash` string).

Whether a gated call can be read back, and with which sentence, is
`agent_host.naming`'s; what the gate then answers is `agent_host.decision`'s.
The gate and the wrapper both come through this one function, so what is
read back and what is released can never be judged by two sets of rules.

The class `self` is Jarvis's own rules: the gate, the wrapper, their rules,
settings and configuration, `authorized_keys`, Claude Code's user and
managed settings, the user's instructions, agents, skills and plugins, the
shell start-up files, every file a configured hook runs, and any checkout's
`.claude/settings*.json`. A write to one of them, by `Edit`, `Write` or a
shell command, is always desk-only and never pending. The one exception is
the fixed command `sudo <confirm program> on|off [class]`, matched exactly.

**What this is, and what it is not (D40).** These are rules over the text
of a call. They will never read a shell command exactly as bash does, and
after two reviews and their patch rounds that is the standing description,
not a list of things still to fix: the gate is a seatbelt against mistakes
(a misheard sentence, an agent that tries a gated action in a shape the
rules know), not a wall against an agent that has been turned against its
own host. Where the rules cannot fully understand a command they refuse
it, which closes the roads somebody thought of. A script in the
repository, a `make` target, an alias in a shell start-up file or a
program the rules have never heard of goes around it; there the
classifier, the user's hooks and the agent's own judgement stand, as they
do for the coding session.

Looking at the surroundings is allowed here and only here: *that* a bare
`git push` is gated depends on where it would land. Naming a target from
the surroundings is never allowed (1b2).
"""
from __future__ import annotations

import dataclasses
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
from agent_host.config import CONFIRM_CLASSES, AgentHostConfig
from agent_host.rules import Program, Rules

_REPO_RE = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
_REMOTE_NAME_RE = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]*")
# A bracketed host is taken whole, whatever is in it (`[fe80::1%eth0]:`): a
# copy to it is outward, and the target shapes decide whether it is named.
_REMOTE_SPEC_RE = re.compile(r"(?:[^@/:]+@)?(\[[^\]/]*\]|[^/:\[\]]+):")
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_GLOB_RE = re.compile(r"[*?\[]")
_NOT_PLAIN_RE = re.compile(r"[\"'\\$*?\[\]{}~]")
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
_PUSH_VALUE_OPTIONS = ("--repo", "--receive-pack", "--exec", "--push-option")


RULE_CONFIRM_SWITCH = "self:confirm-switch"


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
        """The class `self` is never confirmable by voice, with one
        exception: the fixed confirmation switch (D36)."""
        return "self" in self.classes and self.rule != RULE_CONFIRM_SWITCH


UNGATED = Verdict()


GitRunner = Callable[[list[str], Path], "str | None"]


def run_git(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> str | None:
    """`git <args>` in `cwd`: its output, or None when it fails for any reason."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5,
            stdin=subprocess.DEVNULL, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def git_runner(env: dict[str, str]) -> GitRunner:
    """A `git` that runs in the given environment. The gate inherits the
    turn's environment from Claude Code; the wrapper's own comes from sshd,
    so it passes the turn's (agent_host.identity) to see what the gate saw."""
    return lambda args, cwd: run_git(args, cwd, env)


# --- the protected paths of the class `self` ------------------------------------

@dataclass(frozen=True)
class Protected:
    """Paths that are Jarvis's own rules. A directory covers its tree.

    Every path is held twice, as written in the configuration and as its
    real path, and a candidate is compared both ways: as it stands in the
    call (made absolute, `..` folded, symlinks NOT followed) and as its real
    path. A protected file reached through a symlink is protected, and so is
    a symlink that stands where a protected file should be.
    """
    paths: frozenset[str]
    # Directories protected as a place: a write into one, or a command that
    # is not a plain read run from inside one, is a write to `self`.
    dirs: frozenset[str] = frozenset()

    def is_protected_dir(self, path: str | os.PathLike) -> bool:
        for form in self._forms(path):
            if form in self.dirs or os.path.basename(form) == ".claude":
                return True
        return False

    @staticmethod
    def _forms(path: str | os.PathLike) -> tuple[str, str]:
        lexical = os.path.normpath(os.path.abspath(path))
        return lexical, os.path.realpath(path)

    def covers(self, path: str | os.PathLike) -> bool:
        for form in self._forms(path):
            if _is_claude_settings(form) or _is_git_control(form):
                return True
            if any(form == p or form.startswith(p + os.sep) for p in self.paths):
                return True
        return False

    def holds(self, path: str | os.PathLike) -> bool:
        """`path` is a directory with a protected path somewhere inside it."""
        for form in self._forms(path):
            prefix = form.rstrip(os.sep) + os.sep
            if any(p.startswith(prefix) for p in self.paths):
                return True
            if os.path.basename(form) == ".claude" or os.path.lexists(os.path.join(form, ".claude")):
                return True
        return False

    def parent_of_one(self, path: str | os.PathLike) -> bool:
        for form in self._forms(path):
            if os.path.basename(form) == ".claude":
                return True
            if any(os.path.dirname(p) == form for p in self.paths):
                return True
        return False


def _is_claude_settings(real: str) -> bool:
    """Any checkout's `.claude/settings*.json`, and the user's own."""
    return (
        os.path.basename(os.path.dirname(real)) == ".claude"
        and fnmatch.fnmatchcase(os.path.basename(real), "settings*.json")
    )


_GIT_CONTROL = frozenset({"config", "config.worktree", "hooks", "info"})


def _is_git_control(real: str) -> bool:
    """A repository's configuration, hooks or info (`.git/config`,
    `.git/hooks/…`, `.git/info/…`, also under `.git/worktrees/<name>/` and
    `.git/modules/<name>/`): a write there decides where a later push goes
    and which commands git runs, so it is Jarvis's own rules too."""
    parts = real.split(os.sep)
    if ".git" not in parts:
        return False
    after = parts[parts.index(".git") + 1:]
    while len(after) >= 2 and after[0] in ("worktrees", "modules"):
        after = after[2:]
    return bool(after) and after[0] in _GIT_CONTROL


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
                found.add(os.path.normpath(expanded))
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
        config.confirm_program,
        # The gate's state, the pending calls, the records and the action
        # log: what a grant was used for and what was read back. Out of
        # reach of the agent's tools (D40 says what that is worth).
        config.state_dir,
    }
    if config.second_prompt_path is not None:
        paths.add(config.second_prompt_path)
    paths |= {home / rel for rel in rules.self_home_paths}
    paths |= {claude_dir / rel for rel in rules.self_claude_dir_paths}
    real = {os.path.realpath(p) for p in paths}
    real |= {os.path.normpath(os.path.abspath(p)) for p in paths}
    for settings in (config.user_settings_path, claude_dir / "settings.local.json",
                     config.managed_settings_path, config.turn_settings_path):
        real |= hook_files(settings, home)
    dirs = {home / rel if rel else home for rel in rules.self_home_dirs} | {claude_dir}
    both = {os.path.realpath(d) for d in dirs} | {os.path.normpath(os.path.abspath(d)) for d in dirs}
    return Protected(frozenset(real), frozenset(both))


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
    # The root-owned program that switches confirmation (D36).
    confirm_program: str = ""


def context_for(
    config: AgentHostConfig, rules: Rules, *, cwd: Path, home: Path, lib_dir: Path,
    git: GitRunner = run_git,
) -> Context:
    """`home` is the passwd entry's home and the temp roots are the rules':
    nothing a safety decision rests on is taken from the process environment
    (`HOME`, `TMPDIR`), which is the one thing project settings could touch."""
    roots = tuple(rules.temp_roots)
    return Context(
        rules=rules,
        cwd=cwd,
        home=home,
        own_owners=frozenset(owner.lower() for owner in config.own_owners),
        protected=protected_paths(config, rules, home=home, lib_dir=lib_dir),
        temp_roots=tuple(os.path.realpath(root) for root in roots if root),
        git=git,
        confirm_program=str(config.confirm_program),
    )


# --- the entry point ------------------------------------------------------------

RULE_NOT_UNDERSTOOD = "compound:not-understood"


def classify(tool_name: str, tool_input: dict, ctx: Context) -> Verdict:
    rules = ctx.rules
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            raise ValueError("a Bash call without a command string")
        return _classify_bash(command, ctx)

    if tool_name in rules.tools_write:
        path = tool_input.get("file_path", tool_input.get("notebook_path"))
        if not isinstance(path, str) or not path:
            raise ValueError(f"a {tool_name} call without a path")
        if ctx.protected.covers(_resolve(path, ctx)):
            return Verdict(gated=True, classes=("self",), rule="self:write")
        return UNGATED

    if tool_name.startswith("mcp__"):
        name = tool_name.lower()
        for word in rules.mcp_words:
            if word in name:
                cls = next((c for w, c in rules.mcp_classes if w in name), "tool")
                return Verdict(gated=True, classes=(cls,), rule=f"mcp:{word}")
        return UNGATED

    if tool_name in rules.tools_ungated:
        return UNGATED
    # A built-in tool nobody decided about (an artifact, a message to
    # another agent, a schedule, a skill, a workflow ...): default-deny.
    return Verdict(gated=True, rule="tool:not-listed")


def _resolve(path: str, ctx: Context) -> str:
    expanded = _expand_home(path, ctx.home)
    if not os.path.isabs(expanded):
        expanded = os.path.join(str(ctx.cwd), expanded)
    return os.path.normpath(expanded)


# --- Bash -----------------------------------------------------------------------

def is_harmless(words, ctx: Context) -> bool:
    """One piece of a compound command is a known-harmless read program:
    the program itself, by its plain name, with nothing in front of it."""
    rules = ctx.rules
    if not words:
        return True
    program = words[0]
    if program in rules.harmless:
        # A reader that can be told to run a program is no reader then.
        return not any(word.startswith(_RUNS_A_PROGRAM.get(program, ())) for word in words[1:]) \
            if program in _RUNS_A_PROGRAM else True
    return program == "git" and len(words) > 1 and words[1] in rules.harmless_git


def _classify_bash(command: str, ctx: Context) -> Verdict:
    rules = ctx.rules
    switch = _confirm_switch(command, ctx)
    if switch is not None:
        return Verdict(gated=True, classes=("self",), rule=RULE_CONFIRM_SWITCH,
                       command=shell.Command(switch, (("root", "sudo"),)))
    if _shell_writes_protected(command, ctx):
        return Verdict(gated=True, classes=("self",), rule="self:shell")

    everyday = _everyday_form(command, ctx)
    if everyday is not None:
        return everyday

    analysis = _analyse(command, ctx)

    if analysis.refuse:
        return Verdict(gated=True, run_alone=True, rule=RULE_NOT_UNDERSTOOD)
    if not analysis.compound and _expands_in_a_write(command, analysis, ctx):
        return Verdict(gated=True, run_alone=True, rule=RULE_NOT_UNDERSTOOD)

    if analysis.compound:
        # Not one command the gate can follow. A gated candidate decides
        # what the refusal says; whether the call runs at all is decided by
        # its pieces: every one a known-harmless read program, or none of it.
        if analysis.root_word:
            return Verdict(gated=True, classes=("root",), run_alone=True, rule="root:any-position")
        for candidate in analysis.commands:
            verdict = _judge(candidate, ctx)
            if verdict.gated and verdict.rule != RULE_NOT_UNDERSTOOD:
                return Verdict(gated=True, classes=verdict.classes, run_alone=True,
                               rule=verdict.rule, command=candidate)
        # Without a quote, a backslash, a variable or a pattern anywhere in
        # the call, its words are exactly what the shell will see, and a
        # piece that is a read of a default-gated program (`kubectl get
        # pods | grep web`) is as well understood as `ls`. With one of
        # them, only the harmless list counts: its arguments are data.
        # A redirection that is left after the harmless ones were taken out
        # puts a file name where a program name would be read: `ls; >cat rm x`.
        if re.search(r"[<>]", shell.without_benign_redirects(command)):
            return Verdict(gated=True, run_alone=True, rule=RULE_NOT_UNDERSTOOD)
        plain = not _NOT_PLAIN_RE.search(command)
        for segment in analysis.segments:
            if is_harmless(segment, ctx):
                continue
            if plain and segment[0] in rules.programs \
                    and not _judge(shell.Command(tuple(segment)), ctx).gated:
                continue
            return Verdict(gated=True, run_alone=True, rule=RULE_NOT_UNDERSTOOD)
        return UNGATED

    return _single_verdict(analysis, ctx)


def _expands_in_a_write(command: str, analysis: shell.Analysis, ctx: Context) -> bool:
    """One command that is not a plain reader and holds something the shell
    still expands (a variable, `~user`, a brace expansion): which path or
    which word it then acts on is not in the call. Written out, it is read."""
    if not analysis.commands:
        return False
    program = analysis.commands[0].program
    if program in ctx.rules.harmless or program in ctx.rules.self_read_programs:
        return False
    text = command.replace("${HOME}", str(ctx.home)).replace("$HOME", str(ctx.home))
    return shell.has_expansion(text)


def _analyse(command: str, ctx: Context) -> shell.Analysis:
    rules = ctx.rules
    return shell.analyse(
        command, known_programs=rules.known_programs, root_programs=rules.root_programs,
        shells=rules.shells, indirect=rules.indirect,
    )


def _single_verdict(analysis: shell.Analysis, ctx: Context) -> Verdict:
    """The verdict on a call that `shell.analyse` read as one command."""
    if not analysis.commands:
        return UNGATED
    inner = analysis.commands[0]
    remote = any(kind == "ssh" for kind, _ in inner.wrappers)
    verdict = _judge(inner, ctx, remote=remote)
    if verdict.run_alone:
        return verdict
    classes = list(verdict.classes)
    is_root = analysis.root_word or any(kind == "root" for kind, _ in inner.wrappers)
    if is_root and "root" not in classes:
        classes.insert(0, "root")
    if not verdict.gated and remote and not _remote_read(inner, ctx):
        # A command on another host that is not a plain read: its paths
        # mean nothing here and nothing about it can be checked locally.
        # Class `outward`; no sentence unless a host-local rule names it.
        classes.append("outward")
        return Verdict(gated=True, classes=tuple(classes), rule="ssh:remote-command", command=inner,
                       run_alone=analysis.quoted_separator)
    if not verdict.gated and not is_root:
        return UNGATED
    # D36: a gated call with a separator anywhere in its string, inside
    # quotes too, is not read back.
    return Verdict(gated=True, classes=tuple(classes), rule=verdict.rule or "root:any-position",
                   command=inner, run_alone=analysis.quoted_separator)


def _confirm_switch(command: str, ctx: Context) -> tuple[str, ...] | None:
    """The one fixed command of the class `self` that can be confirmed by
    voice: exactly `sudo <confirm program> on|off [class]`, written out, with
    nothing before, between or after. Every other form that names the
    program is an ordinary write to Jarvis's own rules: desk-only.

    Returns (program, program, state[, class]) shaped as the argv after
    `sudo`, or None.
    """
    if not ctx.confirm_program or shell.has_separator(command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if len(words) not in (3, 4) or words[0] != "sudo" or words[1] != ctx.confirm_program:
        return None
    if words[2] not in ("on", "off"):
        return None
    if len(words) == 4 and words[3] not in CONFIRM_CLASSES:
        return None
    return ("sudo", *words[1:])


# --- three everyday forms of a compound command, read in full ---------------------------
#
# What the gate cannot fully understand does not get through. These forms
# it can: each is taken apart completely, every part is judged by the same
# rules as a call of its own, and anything that does not fit exactly falls
# back to the refusal. None of them makes a gated command confirmable: a
# gated command in one of these forms is still "run it alone" (D36).

_HEREDOC_RE = re.compile(r"(?P<before>[^\n]*?)<<(?P<dash>-?)[ \t]*(?P<quote>['\"]?)"
                         r"(?P<word>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)(?P<after>[^\n]*)")
_CAT_HEREDOC_RE = re.compile(
    r"(?P<before>[^\n]*)\"\$\(cat <<'(?P<word>[A-Za-z_][A-Za-z0-9_]*)'\n"
    r"(?P<body>.*?)\n(?P=word)\n\)\"(?P<after>[^\n]*)", re.DOTALL)
_LITERAL_DIR_RE = re.compile(r"[^$*?\[\]{}`]+")


def _alone(verdict: Verdict) -> Verdict:
    """A gated command inside a compound form is never read back."""
    if verdict.gated and not verdict.run_alone:
        return dataclasses.replace(verdict, run_alone=True)
    return verdict


def _one_command(text: str, ctx: Context) -> Verdict | None:
    """The verdict on `text` as one command by the ordinary rules, or None
    when it is not one command the gate can follow."""
    if _shell_writes_protected(text, ctx):
        return Verdict(gated=True, classes=("self",), rule="self:shell")
    analysis = _analyse(text, ctx)
    if analysis.compound or analysis.refuse:
        return None
    if _expands_in_a_write(text, analysis, ctx):
        return Verdict(gated=True, run_alone=True, rule=RULE_NOT_UNDERSTOOD)
    return _single_verdict(analysis, ctx)


def _plain_program(text: str, ctx: Context) -> list[str] | None:
    """The words of `text` when it is one command that is not a shell, an
    interpreter, a wrapper or a program that runs what it reads from stdin:
    the program itself, by its plain name, as the first word."""
    rules = ctx.rules
    try:
        words = shlex.split(text)
    except ValueError:
        return None
    if not words:
        return None
    stripped, ok = shell.strip_prefixes(words)
    program = words[0]
    if not ok or stripped != words or "/" in program:
        return None
    if program in rules.shells | rules.indirect | rules.root_programs | rules.stdin_runners \
            or program in ("ssh", "env") or _is_interpreter(program, ctx):
        return None
    return words


def _everyday_form(command: str, ctx: Context) -> Verdict | None:
    text = shell.without_benign_redirects(command)
    if "\n" in text or "\r" in text:
        return _heredoc_form(text, ctx)
    if shell.substitutes(text):
        return None
    split = shell.top_level(text)
    if split is None:
        return None
    pieces, operators = split
    if operators == ["&&"]:
        return _cd_form(pieces[0], pieces[1], ctx)
    if operators and all(operator == "|" for operator in operators):
        return _filter_form(pieces, ctx)
    return None


def _cd_form(first: str, second: str, ctx: Context) -> Verdict | None:
    """`cd <dir> && <one command>`: the directory is a literal path, and the
    command is judged as a call of its own made in that directory, with all
    the path rules."""
    try:
        words = shlex.split(first)
    except ValueError:
        return None
    if len(words) != 2 or words[0] != "cd":
        return None
    directory = words[1]
    if not _LITERAL_DIR_RE.fullmatch(directory) or directory.startswith("-"):
        return None
    if directory.startswith("~") and not (directory == "~" or directory.startswith("~/")):
        return None
    resolved = os.path.realpath(_resolve(directory, ctx))
    if not os.path.isdir(resolved):
        return None
    if ".." in directory.split("/"):
        # `..` may walk around inside the checkout, not out of it.
        top = git_top_level(ctx.cwd)
        root = os.path.realpath(top) if top is not None else None
        if root is None or not (resolved == root or resolved.startswith(root + os.sep)):
            return None
    there = dataclasses.replace(ctx, cwd=Path(resolved))
    verdict = _one_command(second, there)
    return None if verdict is None else _alone(verdict)


def _filter_form(pieces: list[str], ctx: Context) -> Verdict | None:
    """`<one command> | <filter> [| <filter> ...]`: the left side is judged
    as a call of its own; every filter is on the list."""
    rules = ctx.rules
    for piece in pieces[1:]:
        try:
            words = shlex.split(piece)
        except ValueError:
            return None
        if not words or words[0] not in rules.filters:
            return None
        if any(">" in word or "<" in word for word in words):
            return None
        if any(word.startswith(_RUNS_A_PROGRAM.get(words[0], ("\0",))) for word in words[1:]):
            return None  # `rg --pre`, `sort --compress-program`: no filter any more
        if words[0] == "tee":
            targets = [word for word in words[1:] if not word.startswith("-")]
            if any(_outside_roots(target, ctx) for target in targets):
                return None
    verdict = _one_command(pieces[0], ctx)
    return None if verdict is None else _alone(verdict)


def _heredoc_form(text: str, ctx: Context) -> Verdict | None:
    """A here-document into one command that is not a shell, an interpreter
    or a wrapper (`git commit -F - <<'EOF'`), and exactly
    `git commit ... "$(cat <<'EOF' ... EOF)" ...`: the text is data for one
    program, and that one command is judged."""
    if "<<<" in text:
        return None  # a here-string is never a here-document
    cat = _CAT_HEREDOC_RE.fullmatch(text.rstrip("\n"))
    if cat is not None:
        # The here-document ends at the FIRST line that is the delimiter: a
        # body that holds such a line ended there, and the rest is commands.
        if cat.group("word") in cat.group("body").split("\n"):
            return None
        reduced = cat.group("before") + '"x"' + cat.group("after")
        if shell.substitutes(reduced) or shell.has_separator(reduced):
            return None
        words = _plain_program(reduced, ctx)
        if words is None or words[:2] != ["git", "commit"]:
            return None
        verdict = _one_command(reduced, ctx)
        return None if verdict is None else _alone(verdict)

    lines = text.split("\n")
    match = _HEREDOC_RE.fullmatch(lines[0])
    if match is None:
        return None
    word = match.group("word")
    body: list[str] = []
    for index, line in enumerate(lines[1:], start=1):
        if (line.strip("\t") if match.group("dash") else line) == word:
            if any(rest.strip() for rest in lines[index + 1:]):
                return None  # something follows the here-document: another command
            break
        body.append(line)
    else:
        return None  # never terminated
    if not match.group("quote") and any("$" in line or "`" in line for line in body):
        return None  # an unquoted here-document expands what is in it
    reduced = match.group("before") + " " + match.group("after")
    if shell.substitutes(reduced) or shell.has_separator(reduced):
        return None
    if _plain_program(reduced, ctx) is None:
        return None
    verdict = _one_command(reduced, ctx)
    return None if verdict is None else _alone(verdict)


def _remote_read(command: shell.Command, ctx: Context) -> bool:
    """The remote command of an `ssh` call is a read: a harmless program, or
    a read verb of a program that is gated by default (`ssh jumpy kubectl
    get pods`)."""
    if any(">" in arg or "<" in arg for arg in command.argv):
        return False  # a redirection on the other host writes there
    return is_harmless(command.argv, ctx) or command.program in ctx.rules.programs


# Options with which a program that otherwise only reads runs another one.
_RUNS_A_PROGRAM = {"rg": ("--pre",), "sort": ("--compress-program",)}
_AWK = frozenset({"awk", "gawk", "mawk", "nawk"})
_AWK_REACHES_OUT = ("system(", "getline", "|", ">")
# A sed script with the `e` command (run a command) or `w` / `r` (write or
# read a file): after an address or a separator, or as a flag of `s///`.
_SED_REACHES_OUT_RE = re.compile(
    r"(?:^|[;{}\s]|\d|\$|/)\s*[erwRW](?:\s|$)|s(.).*\1.*\1[a-zA-Z0-9]*[ew]")


# Per program: short options whose value is a script, short options whose
# value is a file of code (or a library), the other short options that take a
# value, and the same as long options.
_SCRIPT_OPTIONS = {
    "awk": ("e", "fiElE", "vF", ("--source",), ("--file", "--include", "--load", "--exec"),
            ("--assign", "--field-separator")),
    "sed": ("e", "f", "li", ("--expression",), ("--file",), ("--line-length", "--in-place")),
}


def _awk_sed_parts(program: str, args: list[str]) -> tuple[list[str], bool]:
    """The script texts of an `awk` or `sed` call, also the ones given as an
    option value (`-e'1e id'`, `--expression=…`, `-ne …`), and whether it
    loads code from a file."""
    key = "awk" if program in _AWK else "sed"
    script_short, file_short, value_short, script_long, file_long, value_long = _SCRIPT_OPTIONS[key]
    scripts: list[str] = []
    loads = False
    take: str | None = None  # what the next argument is: "script", "file" or "value"
    for arg in args:
        if take is not None:
            if take == "script":
                scripts.append(arg)
            loads = loads or take == "file"
            take = None
            continue
        if arg == "--" or not arg.startswith("-") or arg == "-":
            if arg != "--":
                scripts.append(arg)  # a script, a file or an assignment: all are looked at
            continue
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if any(long.startswith(name) and len(name) > 3 for long in script_long):
                if eq:
                    scripts.append(value)
                else:
                    take = "script"
            elif any(long.startswith(name) and len(name) > 3 for long in file_long):
                loads = True
                take = None if eq else "file"
            elif name in value_long and not eq and name != "--in-place":
                take = "value"
            continue
        for index, letter in enumerate(arg[1:], start=1):
            rest = arg[index + 1:]
            if letter in script_short:
                if rest:
                    scripts.append(rest)
                else:
                    take = "script"
                break
            if letter in file_short:
                loads = True
                take = None if rest else "file"
                break
            if letter in value_short:
                if key == "sed" and letter == "i":
                    break  # -i[SUFFIX]: the suffix is attached or absent
                take = None if rest else "value"
                break
    return scripts, loads


def _script_reaches_out(program: str, args: list[str]) -> bool:
    """`awk` and `sed` are little interpreters: a script that runs a
    command or writes a file is inline code."""
    if program not in _AWK and program != "sed":
        return False
    scripts, loads = _awk_sed_parts(program, args)
    if loads:
        return True
    if program in _AWK:
        return any(mark in script for script in scripts for mark in _AWK_REACHES_OUT)
    return any(_SED_REACHES_OUT_RE.search(script) for script in scripts)


def _is_interpreter(program: str, ctx: Context) -> bool:
    return any(fnmatch.fnmatchcase(program, pattern) for pattern in ctx.rules.interpreters)


def _inline_code(args: list[str], ctx: Context) -> bool:
    """An interpreter call that carries its code in the call, or reads it
    from stdin. Looked for anywhere in the arguments: which of them is the
    script and which the value of an option is not something to guess at."""
    rules = ctx.rules
    positional = False
    for arg in args:
        if arg in rules.inline_words:
            return True
        if arg.startswith("--"):
            if arg.split("=", 1)[0] in rules.inline_words:
                return True
        elif arg.startswith("-") and len(arg) > 1:
            if any(letter in rules.inline_letters for letter in arg[1:]):
                return True
        else:
            positional = True
    return not positional  # no script at all: the program comes from stdin


def _judge(command: shell.Command, ctx: Context, *, remote: bool = False) -> Verdict:
    """One command, already unwrapped: is it gated, and in which classes."""
    argv = command.argv
    if not argv:
        return UNGATED
    rules = ctx.rules
    program = shell.program_name(argv[0])
    args = list(argv[1:])

    if program in rules.root_programs:
        return Verdict(gated=True, classes=("root",), rule=f"root:{program}")
    if program in rules.shells:
        # What `shell.analyse` did not unwrap: no `-c`. With a script file
        # it is not followed (D40); without one it reads commands from stdin.
        script = [arg for arg in args if not arg.startswith(("-", "+"))]
        if not script or "-s" in args:
            return Verdict(gated=True, rule=f"shell:{program}:stdin")
        return UNGATED
    if _is_interpreter(program, ctx):
        if _inline_code(args, ctx):
            return Verdict(gated=True, rule=f"interpreter:{program}")
        return UNGATED
    if _script_reaches_out(program, args):
        return Verdict(gated=True, rule=f"interpreter:{program}")
    if program in _RUNS_A_PROGRAM and any(arg.startswith(_RUNS_A_PROGRAM[program]) for arg in args):
        return Verdict(gated=True, rule=f"interpreter:{program}")
    if any(name.startswith("GIT_CONFIG") for name in command.env):
        # Configuration for git through the environment: an alias, a
        # command git runs or another address, set where no option shows it.
        return Verdict(gated=True, run_alone=True, rule=RULE_NOT_UNDERSTOOD)
    if program.startswith("git-") and program != "git-lfs":
        # `git-push`, `/usr/lib/git-core/git-push`: git by another name.
        return _judge_git([program[len("git-"):], *args], ctx, command.env)
    if program == "git":
        return _judge_git(args, ctx, command.env)
    if program in ("rm", "find") and remote:
        return UNGATED  # judged by the caller as a remote command, never against local paths
    if program == "rm":
        return _judge_rm(args, ctx)
    if program == "find":
        return _judge_find(args, ctx)
    if program in rules.transfer_programs:
        return _judge_transfer(program, args, ctx)
    if program in rules.http:
        return _judge_http(program, args, ctx, command.env)
    if program in rules.programs:
        return _judge_default(rules.programs[program], args, ctx, command.env)
    # A program that is not gated by default: an action rule is what gates
    # it. Its words are its arguments up to the first option, its verb the
    # first of them (`factory build jarvis`: path `build`).
    words: list[str] = []
    for arg in args:
        if arg.startswith("-"):
            break
        words.append(arg)
    classes = _action_classes(program, tuple(words), ctx, verb=words[0] if words else None)
    if classes:
        return Verdict(gated=True, classes=classes, rule=f"action:{program}")
    if program not in rules.harmless and any(
        shell.program_name(arg) in rules.known_programs | rules.shells
        or _is_interpreter(shell.program_name(arg), ctx)
        or shell.program_name(arg) in _AWK | {"sed"}
        for arg in args
    ):
        # A program no rule knows, with a program the rules do know among
        # its arguments (`watchdog sudo ...`, `runner gh repo delete x`): it
        # may be a wrapper the gate cannot see through.
        return Verdict(gated=True, run_alone=True, rule=RULE_NOT_UNDERSTOOD)
    return UNGATED


def _action_classes(
    program: str, subpath: tuple[str, ...], ctx: Context, *, verb: str | None = None,
) -> tuple[str, ...]:
    classes: list[str] = []
    for action in ctx.rules.actions:
        if action.matches(program, subpath, verb) and action.cls not in classes:
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


def _judge_default(program: Program, args: list[str], ctx: Context,
                   env: tuple[str, ...] = ()) -> Verdict:
    subpath, placed, rest = find_verb(program, args)
    name = program.name
    if not placed:
        return Verdict(gated=True, classes=_option_classes(name, args, ctx),
                       rule=f"default:{name}:option-before-verb")
    if not subpath:
        return UNGATED

    if name == "gh":
        special = _judge_gh(subpath, args, rest, ctx, env)
        if special is not None:
            return special

    if _is_read_verb(program, subpath[-1]):
        return UNGATED

    classes = list(_action_classes(name, subpath, ctx))
    for cls in _option_classes(name, args, ctx):
        if cls not in classes:
            classes.append(cls)
    return Verdict(gated=True, classes=tuple(classes), rule=f"default:{name}:{' '.join(subpath)}")


# --- gh -----------------------------------------------------------------------------

def explicit_repos(args: list[str], repo_options: frozenset[str]) -> list[str | None]:
    """Every value of `--repo` / `-R` as written in the call."""
    found: list[str | None] = []
    for index, word in enumerate(args):
        if word in repo_options:
            found.append(args[index + 1] if index + 1 < len(args) else None)
            continue
        for option in repo_options:
            if option.startswith("--") and word.startswith(option + "="):
                found.append(word[len(option) + 1:])
            elif not option.startswith("--") and word.startswith(option) and len(word) > len(option):
                found.append(word[len(option):])
    return found


def explicit_repo(args: list[str], repo_options: frozenset[str]) -> str | None:
    """The one value of `--repo` / `-R`, or None: when it is absent, and also
    when it is given more than once (which of them `gh` uses is not something
    to rely on)."""
    found = explicit_repos(args, repo_options)
    return found[0] if len(found) == 1 else None


def is_own_repo(repo: str | None, own_owners: frozenset[str]) -> bool:
    if repo is None or not _REPO_RE.fullmatch(repo):
        return False
    return repo.split("/", 1)[0].lower() in own_owners


def _names_a_repo_elsewhere(args: list[str]) -> bool:
    """An argument that itself names a repository: a URL or an ssh address.
    `gh pr checkout https://github.com/them/x/pull/1 --repo me/x` acts on
    the URL's repository, whatever `--repo` says."""
    return any("://" in arg or arg.startswith("git@") or "github.com/" in arg for arg in args)


def _judge_gh(
    subpath: tuple[str, ...], args: list[str], rest: list[str], ctx: Context,
    env: tuple[str, ...] = (),
) -> Verdict | None:
    gh = ctx.rules.gh
    rule = f"default:gh:{' '.join(subpath)}"

    if subpath == ("api",):
        # Flags may stand before the subcommand too (`gh --method=DELETE api x`).
        if _gh_api_writes([arg for arg in args if arg != "api"], ctx):
            return Verdict(gated=True, classes=("api",), rule="gh-api:write")
        return UNGATED

    if subpath in gh.own_repo_paths or subpath in gh.foreign_message_paths:
        if subpath in gh.positional_repo_paths:
            positional = [word for word in rest if not word.startswith("-")]
            repo = positional[0] if positional else None
        else:
            repo = explicit_repo(args, gh.repo_options)
        # The own-repository exception holds only for a call that says
        # plainly, once, which repository it is for: no second --repo, no
        # URL among the arguments, no GH_* variable set in front of it
        # (GH_HOST, GH_REPO and friends change where the call goes).
        plain = (
            len(explicit_repos(args, gh.repo_options)) <= 1
            and not _names_a_repo_elsewhere(args)
            and not any(name.startswith(("GH_", "GITHUB_")) for name in env)
        )
        own = plain and is_own_repo(repo, ctx.own_owners)
        if subpath in gh.own_repo_paths and own:
            return UNGATED
        classes: tuple[str, ...] = ()
        if subpath in gh.foreign_message_paths and not own:
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


# Options that make an HTTP client read its options from somewhere else,
# and the files it reads them from when nothing says otherwise.
_HTTP_CONFIG = {
    "curl": {"short": ("K",), "long": ("--config",), "off": ("-q", "--disable"),
             "files": (".curlrc", ".config/curlrc"), "env": ("CURL_HOME", "XDG_CONFIG_HOME")},
    "wget": {"short": ("e",), "long": ("--execute", "--config"), "off": ("--no-config",),
             "files": (".wgetrc", ".config/wget/wgetrc"), "env": ("WGETRC", "XDG_CONFIG_HOME")},
}


def _judge_http(program: str, args: list[str], ctx: Context, env: tuple[str, ...] = ()) -> Verdict:
    client = ctx.rules.http[program]
    gated = Verdict(gated=True, classes=("api",), rule=f"http:{program}")
    extra = _HTTP_CONFIG.get(program)
    if extra is not None:
        # A configuration the call does not show can turn a GET into
        # anything: options from a file (`curl -K`, `wget -e`), from a
        # variable set in front of the call, or from the user's own rc file
        # unless the call switches that off as curl requires it, first.
        if any(name in extra["env"] for name in env):
            return gated
        off = bool(args) and args[0] in extra["off"] if program == "curl" \
            else any(arg in extra["off"] for arg in args)
        if not off and any(os.path.lexists(ctx.home / rel) for rel in extra["files"]):
            return gated
    positional: list[str] = []
    for word in args:
        if word.startswith("--") and len(word) > 2:
            name = word.split("=", 1)[0]
            if name.startswith("--expand-"):
                name = "--" + name[len("--expand-"):]  # curl: the option, with variables
            if _long_option_matches(name, client.long_options):
                return gated
            if extra is not None and _long_option_matches(name, extra["long"]):
                return gated
        elif word.startswith("-") and len(word) > 1:
            if any(letter in client.short_letters for letter in word[1:]):
                return gated
            if extra is not None and any(letter in extra["short"] for letter in word[1:]):
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

# Per copy program: the short options that take a value (as the next word
# or attached), and those of them, or flags, that change how the host is
# reached or what runs there or here (a proxy, a config file, a program to
# run instead of ssh, a batch of commands, an option for the remote side).
TRANSFER_VALUED = {"scp": "cDFiJlPoSX", "sftp": "BbcDFiJloPRSsX", "rsync": "efTBM"}
TRANSFER_REACH = {"scp": "oFJSD", "sftp": "oFJSDbs", "rsync": "eM"}
TRANSFER_REACH_LONG = ("--rsh", "--rsync-path", "--remote-option")


def transfer_reach(program: str, args: list[str]) -> bool:
    """An option of `scp`, `sftp` or `rsync` that changes how the host is
    reached: a host named in the sentence would not be the whole truth."""
    valued = TRANSFER_VALUED.get(program, "")
    reach = TRANSFER_REACH.get(program, "")
    for arg in args:
        if "proxycommand" in arg.lower() or "proxyjump" in arg.lower():
            return True
        if arg.startswith("--"):
            if arg.split("=", 1)[0] in TRANSFER_REACH_LONG:
                return True
            continue
        if not arg.startswith("-") or arg == "-":
            continue
        for letter in arg[1:]:
            if letter in reach:
                return True
            if letter in valued:
                break  # the rest of the word is its value
    return False


def remote_hosts(program: str, args: list[str], ctx: Context) -> list[str]:
    """The other hosts a copy command names, as written."""
    hosts: list[str] = []
    valued = TRANSFER_VALUED.get(program, "")
    skip = False
    for word in args:
        if skip:
            skip = False
            continue
        if word.startswith("-"):
            if not word.startswith("--") and len(word) > 1:
                for index, letter in enumerate(word[1:], start=1):
                    if letter in valued:
                        skip = index == len(word) - 1  # the value is the next word
                        break
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
    deletes = program == "rsync" and any(arg.startswith("--del") or arg == "--remove-source-files"
                                         for arg in args)
    if remote_hosts(program, args, ctx):
        classes = ("outward", "delete") if deletes else ("outward",)
        return Verdict(gated=True, classes=classes, rule=f"transfer:{program}")
    if deletes:
        # A local rsync that deletes: a recursive delete of its destination.
        positional = [arg for arg in args if not arg.startswith("-")]
        if not positional or _outside_roots(positional[-1], ctx) or (
                "--remove-source-files" in args and any(
                    _outside_roots(source, ctx) for source in positional[:-1])):
            return Verdict(gated=True, classes=("delete",), rule="rsync:delete-outside")
    return UNGATED  # both ends local: the path rule of `self` has already looked


# --- rm -r -------------------------------------------------------------------------------

def _allowed_roots(ctx: Context) -> list[str]:
    allowed = list(ctx.temp_roots)
    top = git_top_level(ctx.cwd)
    if top is not None:
        allowed.append(os.path.realpath(top))
    return allowed


def _outside_roots(target: str, ctx: Context) -> bool:
    """A path a recursive delete names that the gate cannot place inside
    the worktree or a temp root. Anything the shell would still expand (a
    variable, a glob, a brace, `~user`) and any `..` is not placed at all."""
    if any(mark in target for mark in ("$", "`", "*", "?", "[", "{", "}")):
        return True
    if target.startswith("~") and not (target == "~" or target.startswith("~/")):
        return True
    if ".." in target.replace("\\", "/").split("/"):
        return True
    real = os.path.realpath(_resolve(target, ctx))
    return not any(real == root or real.startswith(root + os.sep) for root in _allowed_roots(ctx))


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
            # GNU getopt takes any unambiguous beginning: --r, --re, --rec ...
            if len(word) > 2 and "--recursive".startswith(word):
                recursive = True
        elif "r" in word or "R" in word:
            recursive = True
    if not recursive:
        return UNGATED
    if any(_outside_roots(target, ctx) for target in targets):
        return Verdict(gated=True, classes=("delete",), rule="rm:recursive-outside")
    return UNGATED


_FIND_RUNS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_FIND_WRITES = frozenset({"-fprint", "-fprint0", "-fprintf", "-fls"})


def _judge_find(args: list[str], ctx: Context) -> Verdict:
    """`find` only looks, unless it is told to run or delete: `-exec` and
    its kin run a command the gate does not follow; `-delete` is a
    recursive delete of the places it starts from."""
    if any(arg in _FIND_RUNS for arg in args):
        return Verdict(gated=True, run_alone=True, rule=RULE_NOT_UNDERSTOOD)
    # `find [-H] [-L] [-P] [-D debugopts] [-Olevel] [start...] [expression]`
    index = 0
    while index < len(args):
        if args[index] in ("-H", "-L", "-P"):
            index += 1
        elif args[index] == "-D":
            index += 2
        elif re.fullmatch(r"-O\d*", args[index]):
            index += 1
        elif args[index] == "--":
            index += 1  # the end of find's own options: start paths follow
            break
        else:
            break
    args = args[index:]
    if "-delete" in args:
        starts: list[str] = []
        for arg in args:
            if arg.startswith(("-", "(", "!")):
                break
            starts.append(arg)
        if any(_outside_roots(start, ctx) for start in starts or ["."]):
            return Verdict(gated=True, classes=("delete",), rule="find:delete-outside")
    return UNGATED


# --- git push ---------------------------------------------------------------------------

# Configuration given on the command line that makes git run a command of
# the caller's choosing, or changes where a push goes.
_GIT_DANGEROUS_CONFIG_RE = re.compile(
    r"alias\.|core\.(sshcommand|hookspath|fsmonitor|pager|editor|gitproxy|askpass)$"
    r"|url\..*\.(insteadof|pushinsteadof)$|sequence\.editor$|protocol\."
    r"|.*\.(textconv|external|helper|command|receivepack|uploadpack|clean|smudge|process"
    r"|driver|tool|cmd|pushurl|url|push|mirror)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GitCall:
    subcommand: str | None
    args: tuple[str, ...]
    placed: bool            # the subcommand could be told from the options
    directory: str | None   # the value of the one `-C`, or None
    redirected: bool        # --git-dir, --work-tree, --namespace, -c, --config-env, a second -C
    dangerous_config: bool  # a -c / --config-env that sets an alias, a command or an address


def read_git(args: list[str]) -> GitCall:
    directory: str | None = None
    redirected = dangerous = False
    index = 0
    while index < len(args):
        word = args[index]
        if not word.startswith("-"):
            return GitCall(word, tuple(args[index + 1:]), True, directory, redirected, dangerous)
        name, has_value, attached = word.partition("=")
        if name in ("-c", "--config-env") or (word.startswith("-c") and len(word) > 2
                                               and not word.startswith("--")):
            redirected = True
            if name == "-c" and not has_value:
                value = args[index + 1] if index + 1 < len(args) else ""
                index += 2
            elif name == "--config-env" and not has_value:
                value = args[index + 1] if index + 1 < len(args) else ""
                index += 2
            elif name == "--config-env":
                value = attached
                index += 1
            else:  # -ckey=value
                value = word[2:]
                index += 1
            if _GIT_DANGEROUS_CONFIG_RE.match(value.split("=", 1)[0].strip()):
                dangerous = True
            continue
        if name == "-C":
            if has_value or index + 1 >= len(args) or directory is not None:
                redirected = True
            else:
                directory = args[index + 1]
            index += 2 if not has_value else 1
            continue
        if name in ("--git-dir", "--work-tree", "--namespace"):
            redirected = True
        if has_value and name.startswith("--"):
            index += 1
        elif name in _GIT_VALUE_OPTIONS:
            index += 2
        elif name in _GIT_FLAGS:
            index += 1
        else:
            return GitCall(None, tuple(args[index:]), False, directory, redirected, dangerous)
    return GitCall(None, (), True, directory, redirected, dangerous)


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


_GIT_SUBCOMMAND_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
_GIT_CONFIG_READS = frozenset({
    "--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool",
    "--list", "-l", "--show-origin", "--show-scope", "--name-only",
})
_GIT_CONFIG_WRITES = frozenset({
    "--add", "--replace-all", "--unset", "--unset-all", "--rename-section", "--remove-section",
    "--edit", "-e", "set", "unset", "edit", "rename-section", "remove-section",
})


def _git_config_writes(args: list[str]) -> bool:
    """`git config` changes something: anything but a plain read. A read is
    `--get`, `--list` and their kin, the `get` and `list` subcommands, or a
    single name with nothing behind it."""
    if any(arg in _GIT_CONFIG_WRITES for arg in args):
        return True
    if any(arg.split("=", 1)[0] in _GIT_CONFIG_READS for arg in args):
        return False
    positional = [arg for arg in args if not arg.startswith("-")]
    if positional and positional[0] in ("get", "list"):
        return False
    return len(positional) != 1


def default_branch_names(remote: str | None, ctx: Context, where: Path) -> set[str]:
    """The branch names that count as a default branch for a push to
    `remote`: the configured ones, and the remote's own HEAD when it can be
    read. One function, for the gate's verdict and for the wrapper's
    read-back."""
    defaults = set(ctx.rules.default_branches)
    if remote is not None and _REMOTE_NAME_RE.fullmatch(remote):
        head = ctx.git(["symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"], where)
        if head and head.startswith(remote + "/"):
            defaults.add(head[len(remote) + 1:])
    return defaults


def is_default_destination(destination: str, defaults: set[str]) -> bool:
    return _is_default_destination(destination, defaults)


def _is_default_destination(destination: str, defaults: set[str]) -> bool:
    """A destination that is, or can resolve to, a default branch. git
    completes a short name itself: `main`, `heads/main` and
    `refs/heads/main` are the same branch. A pattern (`refs/heads/*`) can
    land on it too."""
    if "*" in destination:
        return True
    for prefix in ("refs/heads/", "heads/"):
        if destination.startswith(prefix):
            destination = destination[len(prefix):]
            break
    return destination in defaults


# Set in front of a push, these make it run in, or for, another repository
# than the directory says (`env -C`, `env --chdir` and `sudo -D` show up as
# the name `--chdir`).
_GIT_ELSEWHERE = frozenset({"--chdir", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                            "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES"})


def _judge_git(args: list[str], ctx: Context, env: tuple[str, ...] = ()) -> Verdict:
    call = read_git(args)
    if call.dangerous_config:
        # An alias, a command git will run, or another address, set on the
        # command line: whatever the subcommand says, this is not it.
        return Verdict(gated=True, rule="git:config-on-command-line")
    if not call.placed:
        if "push" in args:
            return Verdict(gated=True, rule="push:option-before-subcommand")
        return UNGATED
    rest = list(call.args)
    if call.subcommand == "subtree" and "push" in rest:
        return Verdict(gated=True, rule="push:subtree")
    if call.subcommand in ("send-pack", "receive-pack", "http-push"):
        return Verdict(gated=True, rule="push:plumbing")  # a push by its plumbing
    if call.subcommand == "config" and _git_config_writes(rest):
        # Git's configuration decides where a push goes and which commands
        # git runs; a write to it has no sentence. Desk-only.
        return Verdict(gated=True, rule="git:config-write")
    if call.subcommand != "push":
        # A subcommand that is an alias in this repository's configuration
        # is a command of somebody's choosing under another name.
        if call.subcommand and _GIT_SUBCOMMAND_RE.fullmatch(call.subcommand):
            where_alias = ctx.cwd
            if call.directory is not None and os.path.isdir(_resolve(call.directory, ctx)):
                where_alias = Path(_resolve(call.directory, ctx))
            if ctx.git(["config", "--get", f"alias.{call.subcommand}"], where_alias):
                return Verdict(gated=True, run_alone=True, rule=RULE_NOT_UNDERSTOOD)
        return UNGATED

    push = parse_push(rest)
    classes: list[str] = []
    if push.delete:
        classes.append("delete")
    if push.force:
        classes.append("force")
    if push.wide:
        return Verdict(gated=True, classes=tuple(classes), rule="push:wide")
    if call.redirected or _GIT_ELSEWHERE.intersection(env):
        # --git-dir, --work-tree, -c, `env -C`, GIT_DIR=: which repository
        # and which configuration this push uses is not what the directory says.
        return Verdict(gated=True, classes=tuple(classes), rule="push:redirected")

    # `git -C <dir> push`: the push runs there, so that is where git is asked.
    where = ctx.cwd
    if call.directory is not None:
        where = Path(_resolve(call.directory, ctx))
        if not where.is_dir():
            return Verdict(gated=True, classes=tuple(classes), rule="push:redirected")
    there = dataclasses.replace(ctx, cwd=where)

    remote = push.remote
    if not push.refspecs:
        landing = None if push.repo_option else _bare_push_landing(remote, there)
        if landing is None:
            return Verdict(gated=True, classes=tuple(classes), rule="push:bare-unresolved")
        remote, branch = landing
        destinations = [branch]
    else:
        destinations = push_destinations(push)
        if destinations is None:
            return Verdict(gated=True, classes=tuple(classes), rule="push:refspec-unreadable")

    # A refspec without a colon names the source only. Where it lands is
    # then the same name, unless the configuration maps it somewhere else
    # (`remote.<name>.push`, `push.default = upstream`): not guessed at.
    if push.refspecs and any(":" not in refspec for refspec in push.refspecs):
        mapped = None
        if remote is not None and _REMOTE_NAME_RE.fullmatch(remote):
            mapped = ctx.git(["config", "--get-all", f"remote.{remote}.push"], where)
        default = ctx.git(["config", "--get", "push.default"], where) or ""
        if mapped or default in ("upstream", "tracking"):
            return Verdict(gated=True, classes=tuple(classes), rule="push:mapped")

    defaults = default_branch_names(remote, ctx, where)

    for destination in destinations:
        if destination in ("HEAD", "@"):
            current = ctx.git(["symbolic-ref", "--short", "HEAD"], where)
            if not current:
                return Verdict(gated=True, classes=tuple(classes), rule="push:head-unresolved")
            destination = current
        if _is_default_destination(destination, defaults):
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

def _only_reads(command: str, segments, ctx: Context) -> bool:
    """Every program of the command is on the short list of programs that
    only read, and nothing is redirected or substituted."""
    if any(mark in command for mark in (">", "$(", "`", "<(")):
        return False
    for words in segments:
        stripped, ok = shell.strip_prefixes(words)
        if not ok:
            return False
        if stripped and stripped[0] not in ctx.rules.self_read_programs:
            return False
    return True


def _shell_writes_protected(command: str, ctx: Context) -> bool:
    """A shell command that writes, or may write, to one of Jarvis's own rules.

    The command is not parsed for what it does. It is a write to `self`
    when it is not made of plain readers only and

    - it names a protected path, or a path inside a protected tree;
    - it names a protected *directory* itself (the home directory, the
      Claude Code user directory, `~/.ssh`, any `.claude`): whatever the
      program, that directory is then where something may land (`cp x ~/`,
      `tar -C ~ -x`, `unzip -d ~/.claude`, `curl --output-dir ~`,
      `wget -P ~/.ssh`, `git checkout other -- .claude`);
    - or it is run from inside a protected directory, where a destination
      that the call does not name is that directory (`curl -O`, `git apply`).

    Words are compared with their quotes ignored and with their quotes
    removed (`~/.claude/"settings.json"`, `"$HOME"/.bashrc`).
    """
    rules = ctx.rules
    text = command.replace("${HOME}", str(ctx.home)).replace("$HOME", str(ctx.home))
    segments = shell.lenient_segments(text)
    dequoted = shell.lenient_segments(re.sub(r"[\"'\\\\]", "", text))

    mentioned = False
    for variant in (segments, dequoted):
        for words in variant:
            stripped, _ = shell.strip_prefixes(words)
            program = shell.program_name(stripped[0]) if stripped else ""
            for word in words:
                for part in re.split(r"[=,]", word):
                    if not part or part.startswith("-") and not part.startswith("-/"):
                        continue
                    glob = _GLOB_RE.search(part)
                    if glob:
                        literal = part[:glob.start()]
                        directory = literal if literal.endswith("/") else os.path.dirname(literal)
                        resolved = _resolve(directory or ".", ctx)
                        if ctx.protected.covers(resolved) or ctx.protected.parent_of_one(resolved) \
                                or ctx.protected.is_protected_dir(resolved):
                            mentioned = True
                        continue
                    resolved = _resolve(part, ctx)
                    if ctx.protected.covers(resolved) or ctx.protected.is_protected_dir(resolved):
                        mentioned = True
                    elif ctx.protected.is_protected_dir(os.path.dirname(resolved)) \
                            and not os.path.isdir(resolved):
                        # A file whose directory is a protected place (`~/.gitconfig`,
                        # `~/.claude/keybindings.json`): that is where it lands. An
                        # existing directory there (a checkout in ~) is a place of its own.
                        mentioned = True
                    elif program in rules.self_tree_programs and ctx.protected.holds(resolved):
                        return True
    if not mentioned and not ctx.protected.is_protected_dir(ctx.cwd):
        return False
    return not _only_reads(command, segments, ctx)
