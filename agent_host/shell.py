"""Reading a `Bash` command the way the gate needs it (1.7, 1.8a, D36).

Two answers come out of here, and nothing is decided here: which programs
are gated is `agent_host.classify`'s business.

- *One command.* A string without any separator is split into words like a
  shell would (`shlex`), leading assignments and transparent prefixes
  (`env`, `nohup`, `time`, `timeout` ...) are dropped, and `sudo`, `ssh`,
  `bash -c` and `sh -c` are unwrapped, at any depth: the result is the
  innermost command plus the wrappers around it.
- *More than one command, or one the gate cannot follow.* If the string
  holds `;`, `&`, `|`, `$(`, `<(`, `>(`, a backtick, a newline, a
  here-document or here-string (`<<`), or ANSI-C or locale quoting (`$'`,
  `$"`) **anywhere, inside quotes included**, or starts with a subshell, a
  group or a redirection, or uses `eval`, `xargs`, `source`, `trap`,
  `coproc` or another program that runs its arguments, or a wrapper with
  an option this module does not know, it is not read as one command. It
  is cut at every separator, parenthesis and redirection, quotes are
  ignored, and the pieces go to the classifier, which lets such a call
  through only when **every** piece is a known-harmless read program
  (`ls | wc -l`). Everything else is refused with "run it alone": when the
  gate cannot fully understand a command, it does not let it through. The
  places where a command could start are *candidates*; a gated candidate
  only decides what the refusal says.

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
# `&` covers `&&` and `|` covers `||`. Added on review: `>(`, a
# here-document or here-string (`<<`), and ANSI-C and locale quoting
# (`$'...'`, `$"..."`), which shlex does not read the way bash does.
_SEPARATOR_RE = re.compile(r"[\n\r;&|`]|\$\(|<\(|>\(|<<|\$'|\$\"")
# Where a piece ends in a string that is not one command: at a separator,
# and also at a parenthesis or a redirection.
_CUT_RE = re.compile(r"[\n\r;&|()<>`]")
# A first word like `(gh`, `{`, `>out` or `2>out`: a subshell, a group or a
# redirection in front of the program. Not one command the gate can follow.
_NOT_A_PROGRAM_RE = re.compile(r"[({<>]|\d+[<>]")
_QUOTE_RE = re.compile(r"[\"'{}$]")
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_ODD_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\]\+?=|\+=)")
_GLOB_IN_WORD_RE = re.compile(r"[*?\[\]]")
_VARIABLE_RE = re.compile(r"\$[A-Za-z_{]")
_TILDE_USER_RE = re.compile(r"(?:^|[\s=:])~[A-Za-z_]")
_BRACE_RE = re.compile(r"\{[^{}\s]*(?:,|\.\.)[^{}\s]*\}")
# Redirections that send nothing anywhere: `2>&1`, `>/dev/null`,
# `2>/dev/null`, `&>/dev/null`, `</dev/null`. They are taken out before the
# command is read, so `pytest -q 2>&1` is still one command.
_BENIGN_REDIRECT_RE = re.compile(
    r"(?<![\w$])(?:\d?>&\d|&>>?\s*/dev/null|\d?>>?\s*/dev/null|<\s*/dev/null)(?=\s|$)")
_DURATION_RE = re.compile(r"\d+(?:\.\d+)?[smhd]?")
_NICE_SHORT_RE = re.compile(r"-\d+")

_MAX_DEPTH = 8

# Shell keywords a command can stand behind (`if gh pr merge 1; then ...`).
_KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until"})

# Prefixes that run the rest of the line unchanged, with the options each
# takes: (flags, options with a value, short options whose value may be
# attached). An option that is not listed makes the command one the gate
# cannot follow: where the real command starts would be a guess.
_PREFIXES: dict[str, tuple[frozenset[str], frozenset[str], tuple[str, ...]]] = {
    "command": (frozenset({"-p"}), frozenset(), ()),
    "builtin": (frozenset(), frozenset(), ()),
    "nohup": (frozenset(), frozenset(), ()),
    "exec": (frozenset({"-c", "-l"}), frozenset({"-a"}), ()),
    "setsid": (frozenset({"-c", "-f", "-w", "--ctty", "--fork", "--wait"}), frozenset(), ()),
    "time": (frozenset({"-p", "-a", "-v", "--portability", "--append", "--verbose"}),
             frozenset({"-f", "-o", "--format", "--output"}), ()),
    "stdbuf": (frozenset(), frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
               ("-i", "-o", "-e")),
    "nice": (frozenset(), frozenset({"-n", "--adjustment"}), ("-n",)),
    "ionice": (frozenset({"-t", "--ignore"}),
               frozenset({"-c", "-n", "--class", "--classdata"}), ("-c", "-n")),
    "timeout": (frozenset({"--preserve-status", "--foreground", "-v", "--verbose"}),
                frozenset({"-k", "-s", "--kill-after", "--signal"}), ()),
    "env": (frozenset({"-i", "-", "--ignore-environment", "-0", "--null", "-v", "--debug"}),
            frozenset({"-u", "--unset", "-C", "--chdir"}), ()),
}

_SUDO_VALUE_OPTIONS = frozenset({
    "-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t",
    "--user", "--group", "--host", "--prompt", "--close-from", "--chdir",
    "--chroot", "--command-timeout", "--other-user", "--role", "--type",
})
_SUDO_FLAGS = frozenset({
    "-A", "-b", "-E", "-H", "-i", "-k", "-K", "-n", "-P", "-S", "-s",
    "--askpass", "--background", "--preserve-env", "--set-home", "--login",
    "--reset-timestamp", "--non-interactive", "--preserve-groups", "--stdin", "--shell",
})
_SSH_VALUE_OPTIONS = frozenset(
    "-" + letter for letter in "BbcDEeFIiJLlmOoPpQRSWw"
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
    # The names of the variables set in front of it (`GH_HOST=x gh ...`,
    # `env CURL_HOME=/tmp curl ...`).
    env: tuple[str, ...] = ()
    # A wrapper was given an option that changes who runs the command,
    # where, or through what (`sudo -u bob`, `sudo -D /x`, `env -C /x`,
    # `ssh -o ProxyCommand=...`, `ssh -p 2222`, `ssh -J jump`). The command
    # is judged all the same; a sentence about it would leave that out, so
    # such a call is never read back (agent_host.naming).
    odd_wrapper: bool = False

    @property
    def program(self) -> str:
        return program_name(self.argv[0]) if self.argv else ""


@dataclass(frozen=True)
class Analysis:
    # False: `commands` is exactly the one command of the call (or empty for
    # an empty string). True: the call is not one command the gate can
    # follow; `commands` are the candidates and `segments` its pieces.
    compound: bool
    commands: tuple[Command, ...]
    # A program of the class `root` stands somewhere in the call, in any
    # position (D36: "sudo, su, pkexec, doas, in any position").
    root_word: bool
    # The words of every piece between separators, quotes ignored. The
    # classifier lets a compound call through only when every piece is a
    # known-harmless read program.
    segments: tuple[tuple[str, ...], ...] = ()
    # The program of the call cannot be told at all (a redirection, a
    # subshell or a group in front of it, a pattern in the program word):
    # refused whatever its pieces look like.
    refuse: bool = False
    # One command, with a separator character inside a quoted argument
    # (`grep -E 'a|b' f`, `git commit -m 'a & b'`). It runs like any other
    # single command when no rule gates it; a gated one is refused with
    # "run it alone", because D36 wants no such character anywhere in a
    # call that is read back.
    quoted_separator: bool = False


def without_benign_redirects(command: str) -> str:
    return _BENIGN_REDIRECT_RE.sub(" ", command)


def has_separator(command: str) -> bool:
    return bool(_SEPARATOR_RE.search(command))


# With one of these in the text a quoted argument can still run a command
# or end where shlex does not think it ends: never "only quoted".
_SUBSTITUTIONS = ("`", "$(", "<(", ">(", "<<", "$'", '$"', "\n", "\r")


def separators_only_inside_quotes(command: str) -> bool:
    """Every `;`, `&` and `|` of the command stands inside quotes (or behind
    a backslash), and nothing in it substitutes a command. Then the command
    is one command with separator characters in its arguments."""
    if any(mark in command for mark in _SUBSTITUTIONS):
        return False
    state = ""
    index = 0
    while index < len(command):
        char = command[index]
        if state == "'":
            if char == "'":
                state = ""
        elif state == '"':
            if char == "\\":
                index += 1
            elif char == '"':
                state = ""
        elif char == "\\":
            index += 1
        elif char in "'\"":
            state = char
        elif char in ";&|":
            return False
        index += 1
    return state == ""


def top_level(command: str) -> tuple[list[str], list[str]] | None:
    """The pieces of a command between its unquoted operators, and those
    operators (`&&`, `||`, `|`, `|&`, `;`, `&`), in order. None when a quote
    is left open. The caller has already made sure nothing in the command
    substitutes a command."""
    pieces: list[str] = []
    operators: list[str] = []
    current: list[str] = []
    state = ""
    index = 0
    while index < len(command):
        char = command[index]
        if state == "'":
            current.append(char)
            if char == "'":
                state = ""
        elif state == '"':
            current.append(char)
            if char == "\\" and index + 1 < len(command):
                index += 1
                current.append(command[index])
            elif char == '"':
                state = ""
        elif char == "\\" and index + 1 < len(command):
            current.append(char)
            index += 1
            current.append(command[index])
        elif char in "'\"":
            state = char
            current.append(char)
        elif char in ";&|":
            two = command[index:index + 2]
            operator = two if two in ("&&", "||", "|&") else char
            pieces.append("".join(current))
            operators.append(operator)
            current = []
            index += len(operator) - 1
        else:
            current.append(char)
        index += 1
    if state:
        return None
    pieces.append("".join(current))
    return pieces, operators


def substitutes(command: str) -> bool:
    """Something in the text can run a command inside an argument, or is
    quoting shlex does not read as bash does."""
    return any(mark in command for mark in _SUBSTITUTIONS)


def has_expansion(command: str) -> bool:
    """Something the shell would still expand into a word the gate does not
    see: a variable (outside single quotes), or `~user` or a brace
    expansion (outside all quotes). `$1`, `$?` and friends are not looked
    at; the caller has put the home directory in place of `$HOME`."""
    outside_single: list[str] = []
    outside_all: list[str] = []
    state = ""
    index = 0
    while index < len(command):
        char = command[index]
        if state == "'":
            if char == "'":
                state = ""
        elif state == '"':
            if char == "\\" and index + 1 < len(command):
                index += 1
            elif char == '"':
                state = ""
            else:
                outside_single.append(char)
        elif char == "\\" and index + 1 < len(command):
            index += 1
        elif char in "'\"":
            state = char
            outside_single.append(" ")
            outside_all.append(" ")
        else:
            outside_single.append(char)
            outside_all.append(char)
        index += 1
    text, bare = "".join(outside_single), "".join(outside_all)
    return bool(_VARIABLE_RE.search(text) or _TILDE_USER_RE.search(bare) or _BRACE_RE.search(bare))


def lenient_segments(command: str) -> list[list[str]]:
    """The words of every piece between separators, quotes ignored."""
    segments = []
    for piece in _CUT_RE.split(command):
        words = _QUOTE_RE.sub(" ", piece.replace("\\", "")).split()
        if words:
            segments.append(words)
    return segments


def _skip_options(args: list[str], flags, valued, attached) -> tuple[list[str], bool]:
    """What follows the options of a prefix; False when one is not known."""
    index = 0
    while index < len(args):
        word = args[index]
        if word == "--":
            return args[index + 1:], True
        if not word.startswith("-") or word == "-" and "-" not in flags:
            break
        name, has_value, _ = word.partition("=")
        if word in flags:
            index += 1
        elif word in valued:
            if index + 1 >= len(args):
                return args[index:], False
            index += 2
        elif has_value and name.startswith("--") and name in valued:
            index += 1
        elif any(word.startswith(short) and len(word) > len(short) for short in attached):
            index += 1
        else:
            return args[index:], False
    return args[index:], True


def _strip(argv: list[str]) -> tuple[list[str], bool, list[str]]:
    words = list(argv)
    names: list[str] = []
    while words:
        word = words[0]
        name = program_name(word)
        if _ODD_ASSIGNMENT_RE.match(word):
            return words, False, names  # `A+=x`, `A[0]=x`: not followed
        if _ASSIGNMENT_RE.match(word):
            names.append(word.split("=", 1)[0])
            words = words[1:]
        elif word == "!" or word in _KEYWORDS:
            words = words[1:]
        elif name in _PREFIXES:
            flags, valued, attached = _PREFIXES[name]
            rest = words[1:]
            if name == "command" and rest and rest[0] in ("-v", "-V"):
                return [], True, names  # a question about a command, not a command
            if name == "nice" and rest and _NICE_SHORT_RE.fullmatch(rest[0]):
                rest = rest[1:]
            if name == "env":
                # Options and assignments, in any order.
                while True:
                    rest, ok = _skip_options(rest, flags, valued, attached)
                    if not ok:
                        return words, False, names
                    if rest and _ASSIGNMENT_RE.match(rest[0]):
                        names.append(rest[0].split("=", 1)[0])
                        rest = rest[1:]
                        continue
                    break
                if any(word in ("-C", "--chdir") or word.startswith("--chdir=")
                       for word in words[1:len(words) - len(rest)]):
                    names.append("--chdir")  # the command runs in another directory
            else:
                rest, ok = _skip_options(rest, flags, valued, attached)
                if not ok:
                    return words, False, names
            if name == "timeout":
                if not rest or not _DURATION_RE.fullmatch(rest[0]):
                    return words, False, names
                rest = rest[1:]
            words = rest
        else:
            break
    return words, True, names


def strip_prefixes(argv: list[str]) -> tuple[list[str], bool]:
    """Drop assignments and transparent prefixes (`env`, `nohup`, `time`,
    `timeout 30`, `nice -n 5` ...) from the front.

    The second value is False when a prefix has an option this function does
    not know (`env -S`, `exec --weird`): where the command then starts is a
    guess, and the caller must treat the call as one it cannot follow.
    """
    words, ok, _ = _strip(argv)
    return words, ok


def _has_root_word(words, root_programs: frozenset[str]) -> bool:
    return any(program_name(word) in root_programs for word in words)


def _candidates(
    command: str, wrappers: tuple[tuple[str, str], ...], *,
    known_programs: frozenset[str], follow_all: frozenset[str], everywhere: bool = False,
) -> tuple[Command, ...]:
    """Every place in a compound string where a command could start.

    In each piece between separators that is the first word, after
    prefixes. When that word is a program that runs its own arguments (a
    shell, `ssh`, `sudo`, `xargs` ...), or the prefixes could not be read,
    every later word that is a program some rule knows starts a candidate
    too: `bash -c 'gh pr merge 1; gh repo delete x'` is cut at the
    semicolon, and the merge is found behind `bash -c`. With `everywhere`
    that holds for every piece: the caller could not tell where the program
    of the call is at all (`>out gh repo delete x`).
    """
    out: list[Command] = []
    for words in lenient_segments(command):
        stripped, ok, names = _strip(words)
        if not stripped:
            continue
        out.append(Command(tuple(stripped), wrappers, tuple(names)))
        if everywhere or not ok or program_name(stripped[0]) in follow_all:
            for index in range(1, len(stripped)):
                if program_name(stripped[index]) in known_programs:
                    out.append(Command(tuple(stripped[index:]), wrappers, tuple(names)))
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
    command = without_benign_redirects(command)
    root_word = False

    def compound(text: str, wrappers, everywhere: bool = False, refuse: bool = False) -> Analysis:
        # Quotes mean nothing here, so a root program counts wherever the
        # word stands in the whole call.
        segments = lenient_segments(command)
        all_words = [word for segment in segments for word in segment]
        return Analysis(
            compound=True,
            commands=_candidates(text, wrappers, known_programs=known_programs,
                                 follow_all=follow_all, everywhere=everywhere),
            root_word=root_word or _has_root_word(all_words, root_programs),
            segments=tuple(tuple(segment) for segment in segments),
            refuse=refuse,
        )

    quoted_separator = False
    odd_wrapper = False
    # ssh options that do not change which host is reached, as whom, or how.
    plain_ssh = frozenset({"-t", "-tt", "-T", "-q", "-n", "-x", "-4", "-6", "-v"})

    def one(text: str, wrappers: tuple[tuple[str, str], ...], depth: int) -> Analysis:
        nonlocal quoted_separator
        if depth > _MAX_DEPTH:
            return compound(text, wrappers)
        if has_separator(text):
            if not separators_only_inside_quotes(text):
                return compound(text, wrappers)
            quoted_separator = True
        try:
            words = shlex.split(text, posix=True)
        except ValueError:  # an unbalanced quote: not one command we can read
            return compound(text, wrappers)
        nonlocal root_word
        root_word = root_word or _has_root_word(words, root_programs)
        argv, ok, names = _strip(words)
        if not ok:
            return compound(text, wrappers, everywhere=True)
        if not argv:
            return Analysis(compound=False, commands=(), root_word=root_word,
                            quoted_separator=quoted_separator)
        if _NOT_A_PROGRAM_RE.match(argv[0]) or _GLOB_IN_WORD_RE.search(argv[0]):
            return compound(text, wrappers, everywhere=True, refuse=True)
        return unwrap(argv, text, wrappers, depth, tuple(names))

    def single(argv, wrappers, names, root=None) -> Analysis:
        return Analysis(False, (Command(tuple(argv), wrappers, names, odd_wrapper),),
                        root_word if root is None else root,
                        quoted_separator=quoted_separator)

    def unwrap(argv: list[str], text: str, wrappers, depth: int, names) -> Analysis:
        nonlocal odd_wrapper
        name = program_name(argv[0])

        if _NOT_A_PROGRAM_RE.match(argv[0]) or _GLOB_IN_WORD_RE.search(argv[0]):
            return compound(text, wrappers, everywhere=True, refuse=True)
        if name in indirect:
            return compound(text, wrappers)

        if name in root_programs:
            wrappers = wrappers + (("root", name),)
            if name == "su":
                odd_wrapper = True  # su is always "as somebody", with a shell of its own
                inner = _option_value(argv[1:], ("-c", "--command"))
                if inner is None:
                    return single(argv, wrappers, names, True)
                return one(inner, wrappers, depth + 1)
            rest, ok = _skip_options(argv[1:], _SUDO_FLAGS, _SUDO_VALUE_OPTIONS, ())
            if not ok:
                return compound(text, wrappers, everywhere=True)
            if len(rest) != len(argv) - 1:
                odd_wrapper = True  # sudo with any option: -u, -g, -i, -D ...
            chdir = ("--chdir",) if any(
                word in ("-D", "--chdir") or word.startswith("--chdir=")
                for word in argv[1:len(argv) - len(rest)]) else ()
            rest, ok, more = _strip(rest)
            if not ok:
                return compound(text, wrappers, everywhere=True)
            if not rest:
                return single(argv, wrappers, names, True)
            return unwrap(rest, text, wrappers, depth + 1, names + chdir + tuple(more))

        if name in shells:
            inner = _shell_c_string(argv[1:])
            if inner is None:
                return single(argv, wrappers, names)
            return one(inner, wrappers + (("shell", name),), depth + 1)

        if name == "ssh":
            rest = _after_options(argv[1:], _SSH_VALUE_OPTIONS)
            # ssh also takes options behind the host (`ssh host -t cmd`).
            remote = _after_options(rest[1:], _SSH_VALUE_OPTIONS)
            if not rest or not remote:  # no host, or no remote command
                return single(argv, wrappers, names)
            given = argv[1:len(argv) - len(rest)] + rest[1:len(rest) - len(remote)]
            if any(option not in plain_ssh for option in given):
                odd_wrapper = True
            # ssh joins its arguments with spaces and the remote shell reads
            # the result again: that string is what is judged.
            return one(" ".join(remote), wrappers + (("ssh", rest[0]),), depth + 1)

        return single(argv, wrappers, names)

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
            # With -c the command string is the first argument that is not
            # an option: `bash -c -x -- 'cmd'`, `bash -c -e 'cmd'`.
            rest = args[index + 1:]
            position = 0
            while position < len(rest):
                later = rest[position]
                if later == "--":
                    position += 1
                    break
                if later in _SHELL_VALUE_OPTIONS:
                    position += 2
                elif later.startswith(("-", "+")) and later not in ("-", "+"):
                    position += 1
                else:
                    break
            return rest[position] if position < len(rest) else None
        index += 1
    return None
