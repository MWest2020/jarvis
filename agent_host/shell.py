"""Reading a `Bash` command the way the gate needs it (1.7, 1.8a, D36).

Two answers come out of here, and nothing is decided here: which programs
are gated is `agent_host.classify`'s business.

- *One command.* A string without any separator is split into words like a
  shell would (`shlex`), leading assignments and transparent prefixes
  (`env`, `nohup`, `time`, `timeout` ...) are dropped, and `sudo`, `ssh`,
  `bash -c` and `sh -c` are unwrapped, at any depth: the result is the
  innermost command plus the wrappers around it.
- *More than one command, or one the gate cannot follow.* If the string
  holds `;`, `&`, `|`, `$(`, `<(`, a backtick or a newline **anywhere,
  inside quotes included**, or starts with a subshell, a group or a
  redirection, or uses `eval`, `xargs`, `source` or another program that
  runs its arguments, it is not read as one command. It is cut at every
  separator, parenthesis and redirection, quotes are ignored, and every
  place where a command could start becomes a *candidate*. If the classifier finds one candidate gated, the whole call
  is refused with "run it alone" and never becomes pending: a read-back of
  one sentence cannot cover two commands (D36). If no candidate is gated,
  the call is not the gate's business and runs (`ls | wc -l`).

This is rules over text, built for mistakes and for an agent that follows
its rules (D40). A variable that holds a program name, an alias, a script or
a brace expansion is not followed.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass

# D36's list: `;`, `&&`, `||`, `|`, `&`, `$(`, `<(`, a backtick, a newline.
# `&` covers `&&` and `|` covers `||`.
_SEPARATOR_RE = re.compile(r"[\n\r;&|`]|\$\(|<\(")
# Where a candidate command can start in a string that is not one command:
# after a separator, and also after a parenthesis or a redirection.
_CUT_RE = re.compile(r"[\n\r;&|()<>`]")
# A first word like `(gh`, `{`, `>out` or `2>out`: a subshell, a group or a
# redirection in front of the program. Not one command the gate can follow.
_NOT_A_PROGRAM_RE = re.compile(r"[({<>]|\d+[<>]")
_QUOTE_RE = re.compile(r"[\"'{}$]")
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")

_MAX_DEPTH = 8

# Prefixes that run the rest of the line unchanged.
_PLAIN_PREFIXES = frozenset({"command", "exec", "nohup", "time", "builtin", "stdbuf", "setsid"})
_TIMEOUT_VALUE_OPTIONS = frozenset({"-k", "-s", "--kill-after", "--signal"})
_ENV_FLAGS = frozenset({"-i", "-", "--ignore-environment", "-0", "--null"})
_ENV_VALUE_OPTIONS = frozenset({"-u", "--unset", "-C", "--chdir"})

_SUDO_VALUE_OPTIONS = frozenset({
    "-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t",
    "--user", "--group", "--host", "--prompt", "--close-from", "--chdir",
    "--chroot", "--command-timeout", "--other-user", "--role", "--type",
})
_SSH_VALUE_OPTIONS = frozenset(
    "-" + letter for letter in "BbcDEeFIiJLlmOopQRSWw"
)
_SHELL_VALUE_OPTIONS = frozenset({"-o", "-O", "+o", "+O", "--rcfile", "--init-file"})


def program_name(word: str) -> str:
    """`/usr/bin/gh` and `./gh` are `gh`."""
    return os.path.basename(word)


@dataclass(frozen=True)
class Command:
    """One command as the classifier judges it."""
    argv: tuple[str, ...]
    # Outermost first: ("root", "sudo"), ("ssh", "<host>"), ("shell", "bash").
    wrappers: tuple[tuple[str, str], ...] = ()

    @property
    def program(self) -> str:
        return program_name(self.argv[0]) if self.argv else ""


@dataclass(frozen=True)
class Analysis:
    # False: `commands` is exactly the one command of the call (or empty for
    # an empty string). True: the call is not one command the gate can
    # follow, and `commands` are the candidates.
    compound: bool
    commands: tuple[Command, ...]
    # A program of the class `root` stands somewhere in the call, in any
    # position (D36: "sudo, su, pkexec, doas, in any position").
    root_word: bool


def has_separator(command: str) -> bool:
    return bool(_SEPARATOR_RE.search(command))


def lenient_segments(command: str) -> list[list[str]]:
    """The words of every piece between separators, quotes ignored."""
    segments = []
    for piece in _CUT_RE.split(command):
        words = _QUOTE_RE.sub(" ", piece.replace("\\", "")).split()
        if words:
            segments.append(words)
    return segments


def strip_prefixes(argv: list[str]) -> tuple[list[str], bool]:
    """Drop assignments and transparent prefixes from the front.

    The second value is False when a prefix has an option this function does
    not know: where the command then starts is a guess, and the caller must
    treat the call as one it cannot follow.
    """
    words = list(argv)
    while words:
        word = words[0]
        name = program_name(word)
        if _ASSIGNMENT_RE.match(word) or word == "!":
            words = words[1:]
        elif name in _PLAIN_PREFIXES:
            words = words[1:]
            while words and words[0].startswith("-") and words[0] != "-":
                words = words[1:]
        elif name == "nice":
            words = words[1:]
            if words and words[0] in ("-n", "--adjustment"):
                words = words[2:]
            elif words and words[0].startswith("-"):
                words = words[1:]
        elif name == "timeout":
            words = words[1:]
            while words and words[0].startswith("-"):
                words = words[2:] if words[0] in _TIMEOUT_VALUE_OPTIONS else words[1:]
            words = words[1:]  # the duration
        elif name == "env":
            words = words[1:]
            while words:
                if _ASSIGNMENT_RE.match(words[0]) or words[0] in _ENV_FLAGS:
                    words = words[1:]
                elif words[0] in _ENV_VALUE_OPTIONS:
                    words = words[2:]
                elif words[0].startswith(("--unset=", "--chdir=")):
                    words = words[1:]
                elif words[0].startswith("-"):
                    return words, False
                else:
                    break
        else:
            break
    return words, True


def _has_root_word(words, root_programs: frozenset[str]) -> bool:
    return any(program_name(word) in root_programs for word in words)


def _candidates(
    command: str, wrappers: tuple[tuple[str, str], ...], *,
    known_programs: frozenset[str], follow_all: frozenset[str], everywhere: bool = False,
) -> tuple[Command, ...]:
    """Every place in a compound string where a command could start.

    In each piece between separators that is the first word, after
    prefixes. When that word is a program that runs its own arguments (a
    shell, `ssh`, `sudo`, `xargs`, `find` ...), or the prefixes could not be
    read, every later word that is a program some rule knows starts a
    candidate too: `bash -c 'gh pr merge 1; gh repo delete x'` is cut at the
    semicolon, and the merge is found behind `bash -c`. With `everywhere`
    that holds for every piece: the caller could not tell where the program
    of the call is at all (`>out gh repo delete x`).
    """
    out: list[Command] = []
    for words in lenient_segments(command):
        stripped, ok = strip_prefixes(words)
        if not stripped:
            continue
        out.append(Command(tuple(stripped), wrappers))
        if everywhere or not ok or program_name(stripped[0]) in follow_all:
            for index in range(1, len(stripped)):
                if program_name(stripped[index]) in known_programs:
                    out.append(Command(tuple(stripped[index:]), wrappers))
    return tuple(out)


def analyse(
    command: str, *,
    known_programs: frozenset[str],
    root_programs: frozenset[str],
    shells: frozenset[str],
    indirect: frozenset[str],
) -> Analysis:
    """Read one `Bash` command string. See the module docstring."""
    follow_all = shells | indirect | root_programs | {"ssh", "env"}
    root_word = False

    def compound(text: str, wrappers, everywhere: bool = False) -> Analysis:
        # Quotes mean nothing here, so a root program counts wherever the
        # word stands in the whole call.
        all_words = [word for segment in lenient_segments(command) for word in segment]
        return Analysis(
            compound=True,
            commands=_candidates(text, wrappers, known_programs=known_programs,
                                 follow_all=follow_all, everywhere=everywhere),
            root_word=root_word or _has_root_word(all_words, root_programs),
        )

    def one(text: str, wrappers: tuple[tuple[str, str], ...], depth: int) -> Analysis:
        if depth > _MAX_DEPTH or has_separator(text):
            return compound(text, wrappers)
        try:
            words = shlex.split(text, posix=True)
        except ValueError:  # an unbalanced quote: not one command we can read
            return compound(text, wrappers)
        nonlocal root_word
        root_word = root_word or _has_root_word(words, root_programs)
        argv, ok = strip_prefixes(words)
        if not ok:
            return compound(text, wrappers, everywhere=True)
        if not argv:
            return Analysis(compound=False, commands=(), root_word=root_word)
        if _NOT_A_PROGRAM_RE.match(argv[0]):
            return compound(text, wrappers, everywhere=True)
        return unwrap(argv, text, wrappers, depth)

    def unwrap(argv: list[str], text: str, wrappers, depth: int) -> Analysis:
        name = program_name(argv[0])

        if name in indirect:
            return compound(text, wrappers)

        if name in root_programs:
            wrappers = wrappers + (("root", name),)
            if name == "su":
                inner = _option_value(argv[1:], ("-c", "--command"))
                if inner is None:
                    return Analysis(False, (Command(tuple(argv), wrappers),), True)
                return one(inner, wrappers, depth + 1)
            rest = _after_options(argv[1:], _SUDO_VALUE_OPTIONS)
            rest, ok = strip_prefixes(rest)
            if not ok:
                return compound(text, wrappers)
            if not rest:
                return Analysis(False, (Command(tuple(argv), wrappers),), True)
            return unwrap(rest, text, wrappers, depth + 1)

        if name in shells:
            inner = _shell_c_string(argv[1:])
            if inner is None:
                return Analysis(False, (Command(tuple(argv), wrappers),), root_word)
            return one(inner, wrappers + (("shell", name),), depth + 1)

        if name == "ssh":
            rest = _after_options(argv[1:], _SSH_VALUE_OPTIONS)
            if len(rest) < 2:  # no host, or no remote command
                return Analysis(False, (Command(tuple(argv), wrappers),), root_word)
            # ssh joins its arguments with spaces and the remote shell reads
            # the result again: that string is what is judged.
            return one(" ".join(rest[1:]), wrappers + (("ssh", rest[0]),), depth + 1)

        return Analysis(False, (Command(tuple(argv), wrappers),), root_word)

    return one(command, (), 0)


def _after_options(args: list[str], value_options: frozenset[str]) -> list[str]:
    """What follows the options of `sudo` or `ssh`."""
    index = 0
    while index < len(args):
        word = args[index]
        if word == "--":
            return args[index + 1:]
        if not word.startswith("-") or word == "-":
            break
        index += 2 if word in value_options else 1
    return args[index:]


def _option_value(args: list[str], names: tuple[str, ...]) -> str | None:
    for index, word in enumerate(args):
        if word in names and index + 1 < len(args):
            return args[index + 1]
        for name in names:
            if name.startswith("--") and word.startswith(name + "="):
                return word[len(name) + 1:]
    return None


def _shell_c_string(args: list[str]) -> str | None:
    """The command string of `bash -c '...'` (also `-lc`, `-xec`), or None."""
    index = 0
    while index < len(args):
        word = args[index]
        if word == "--" or not word.startswith(("-", "+")) or word in ("-", "+"):
            return None  # a script file, or stdin: not followed (D40)
        if word in _SHELL_VALUE_OPTIONS:
            index += 2
            continue
        if word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
            return args[index + 1] if index + 1 < len(args) else None
        index += 1
    return None
