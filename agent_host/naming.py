"""From a gated call to the sentence that is read back (1.7c, 1.7e, 1.10, D36).

`derive()` is the one function both the gate and the wrapper call. From the
stored call (tool name, input, real-path `cwd`) and nothing else it gives:
the classes, whether the call can be named, the sentence, and the hash a
confirmation is bound to. The gate uses it to decide whether a call becomes
pending; the wrapper uses it again, from the pending call, to build what
Mark hears. What is heard and what a yes releases are therefore always the
same call, judged by the same rules.

A call is one of three things here:

- **Named**: every class of the call has a template, every target the
  template needs stands explicitly in the call, and each target has the
  strict shape of its kind (agent_host.targets). The sentence is the
  template's fixed words plus those targets, all of them, and nothing else
  from the call or from the agent.
- **Missing**: the call would be nameable but for explicit targets that are
  not in it. The outcome lists exactly those parts ("add --repo
  owner/name"), and which options one repair may add.
- **not nameable** (`None`): no template, an option or a target the
  template cannot say, a target outside its shape, over 100 characters or
  shaped like a secret, more targets than the template holds. It needs the
  desk.

Nothing is ever taken from the surroundings: no repository from the
checkout, no namespace or cluster from the current context, no upstream for
a push, no recipient. `cwd` is used for two things only (D36): the explicit
`--repo` of a `gh` call must equal the checkout's own repository when it has
one on the forge host, and the remote a push *names* is resolved to the
address git will really push to (agent_host.pushaddr).

The sentence is made for the ear: a commit is its first seven hex digits, a
`/` in a ref is "slash", a repository is "name" or "name of owner".
"""
from __future__ import annotations

import fnmatch
import shlex
from dataclasses import dataclass
from pathlib import Path

from agent_host import classify, gatehash, pushaddr, targets
from agent_host.config import CONFIRM_CLASSES, AgentHostConfig
from agent_host.targets import NotNameable, join_spoken

MAX_ITEMS = 3       # refs, names, hosts in one sentence
MAX_RECIPIENTS = 5  # addresses in one sentence

PART_REPO = "add --repo owner/name"
PART_NAMESPACE = "add -n namespace"
PART_PIN = "add --match-head-commit and the full commit"
PART_CONTEXT = "add --context"
PART_KUBE_CONTEXT = "add --kube-context"
PART_REMOTE = "name the remote and the refspec"

SAY_YES = "Say yes to go ahead."


@dataclass(frozen=True)
class Named:
    sentence: str
    # The classes this sentence speaks for.
    covers: frozenset[str]
    # What the wrapper must fetch to complete the read-back, or None:
    # ("merge", number, "owner/name", full sha) or
    # ("push", remote, ((source, destination), ...)).
    status: tuple | None = None


@dataclass(frozen=True)
class Missing:
    parts: tuple[str, ...]
    # The options one repair may add (each with its value), and whether it
    # may add words at the end (a remote and refspecs).
    options: tuple[str, ...] = ()
    words: bool = False


@dataclass(frozen=True)
class Derived:
    verdict: classify.Verdict
    outcome: Named | Missing | None
    # The hash of the call. For a push it includes the resolved push
    # address, and is None when that cannot be resolved.
    hash: str | None
    # The hash without the push address: equal to `hash` for everything
    # that is not a push.
    base_hash: str


# --- option reading for the templates -----------------------------------------------

def read_options(
    args: list[str], *, flags: dict[str, str] | set[str], valued: dict[str, str],
) -> tuple[dict[str, str], set[str], list[str]]:
    """(values by canonical option name, flags present, positional words).

    Strict: an option the template does not know, an option given twice, or
    a valued option without its value makes the call not nameable.
    `valued` maps every spelling to its canonical name.
    """
    flag_names = flags if isinstance(flags, dict) else {name: name for name in flags}
    values: dict[str, str] = {}
    seen: set[str] = set()
    positional: list[str] = []
    index = 0
    while index < len(args):
        word = args[index]
        index += 1
        if word == "--":
            raise NotNameable("an end-of-options marker")
        if not word.startswith("-") or word == "-":
            positional.append(word)
            continue
        name, has_value, attached = word.partition("=")
        if name in valued:
            canonical = valued[name]
            if has_value:
                value = attached
            elif index < len(args):
                value = args[index]
                index += 1
            else:
                raise NotNameable(f"{name} without a value")
            if canonical in values:
                raise NotNameable(f"{name} given twice")
            values[canonical] = value
        elif not has_value and name in flag_names:
            seen.add(flag_names[name])
        else:
            raise NotNameable(f"an option the read-back cannot say: {name}")
    return values, seen, positional


def _after(positional: list[str], subpath: tuple[str, ...]) -> list[str]:
    if tuple(positional[:len(subpath)]) != subpath:
        raise NotNameable("the subcommand is not where the template expects it")
    return positional[len(subpath):]


# --- gh ---------------------------------------------------------------------------------

_REPO_OPTIONS = {"--repo": "repo", "-R": "repo"}


def _checked_repo(value: str, ctx: classify.Context, config: AgentHostConfig) -> targets.Target:
    """The explicit repository of a `gh` call, checked for shape and against
    the checkout it is called from (D36, "consistency")."""
    target = targets.repository(value, config.own_owners)
    checkout = pushaddr.checkout_repository(ctx.cwd, config.forge_host, ctx.git)
    if checkout is None:
        return target
    if checkout == pushaddr.CHECKOUT_UNKNOWN:
        raise NotNameable("the checkout's remote is not a repository on the forge host")
    if checkout.repository.lower() != value.lower():
        raise NotNameable("--repo is not the checkout's repository")
    return target


def _name_gh(subpath, args, rest, ctx, config) -> Named | Missing:
    if subpath == ("pr", "merge"):
        values, _, positional = read_options(
            args,
            flags={"--squash", "-s", "--merge", "-m", "--rebase", "-r", "--delete-branch", "-d"},
            valued={**_REPO_OPTIONS, "--match-head-commit": "pin", "--subject": "subject",
                    "-t": "subject", "--body": "body", "-b": "body"},
        )
        words = _after(positional, subpath)
        if len(words) != 1:
            raise NotNameable("a merge names exactly one pull request")
        number = targets.number(words[0])
        missing, options = [], []
        if "repo" not in values:
            missing.append(PART_REPO)
            options += ["--repo", "-R"]
        if "pin" not in values:
            missing.append(PART_PIN)
            options += ["--match-head-commit"]
        repo = _checked_repo(values["repo"], ctx, config) if "repo" in values else None
        pin = targets.commit(values["pin"], full=True) if "pin" in values else None
        if missing:
            return Missing(tuple(missing), tuple(options))
        return Named(
            f"Merge pull request {number.spoken} on {repo.spoken}, at commit {pin.spoken}.",
            frozenset({"merge"}),
            status=("merge", number.value, repo.value, pin.value),
        )

    if subpath == ("repo", "create"):
        _, flags, positional = read_options(
            args,
            flags={"--public", "--private", "--internal", "--clone", "-c", "--add-readme"},
            valued={"--description": "description", "-d": "description"},
        )
        words = _after(positional, subpath)
        if len(words) != 1:
            raise NotNameable("a repository creation names exactly one repository")
        repo = targets.repository(words[0], config.own_owners)
        visibility = flags & {"--public", "--private", "--internal"}
        if len(visibility) > 1:
            raise NotNameable("more than one visibility")
        # When the call does not say, it is read back as public (D36).
        word = visibility.pop()[2:] if visibility else "public"
        return Named(f"Create the {word} repository {repo.spoken}.", frozenset({"create"}))

    if subpath in (("repo", "delete"), ("repo", "archive")):
        _, _, positional = read_options(args, flags={"--yes", "-y"}, valued={})
        words = _after(positional, subpath)
        if len(words) != 1:
            raise NotNameable("exactly one repository")
        repo = targets.repository(words[0], config.own_owners)
        verb = "Delete" if subpath[1] == "delete" else "Archive"
        return Named(f"{verb} the repository {repo.spoken}.", frozenset({"delete"}))

    if subpath in (("issue", "delete"), ("release", "delete")):
        values, _, positional = read_options(args, flags={"--yes", "-y"}, valued=_REPO_OPTIONS)
        words = _after(positional, subpath)
        if len(words) != 1:
            raise NotNameable("exactly one thing to delete")
        if subpath[0] == "issue":
            what = f"issue {targets.number(words[0]).spoken}"
        else:
            what = f"release {targets.ref(words[0]).spoken}"
        if "repo" not in values:
            return Missing((PART_REPO,), ("--repo", "-R"))
        repo = _checked_repo(values["repo"], ctx, config)
        return Named(f"Delete {what} on {repo.spoken}.", frozenset({"delete"}))

    message_forms = {
        ("pr", "comment"): "Post a comment on pull request {n} on {repo}.",
        ("issue", "comment"): "Post a comment on issue {n} on {repo}.",
        ("pr", "review"): "Post a review on pull request {n} on {repo}.",
        ("pr", "create"): "Open a pull request on {repo}.",
        ("issue", "create"): "Open an issue on {repo}.",
    }
    if subpath in message_forms:
        # The text of a comment is not a target and is not spoken; the
        # options that carry it are not restricted.
        number = None
        if subpath[1] != "create":
            if not rest:
                raise NotNameable("no number")
            number = targets.number(rest[0])
        value = classify.explicit_repo(args, ctx.rules.gh.repo_options)
        if value is None:
            return Missing((PART_REPO,), ("--repo", "-R"))
        repo = _checked_repo(value, ctx, config)
        return Named(
            message_forms[subpath].format(n=number.spoken if number else "", repo=repo.spoken),
            frozenset({"message"}),
        )

    raise NotNameable("no sentence for this gh subcommand")


# --- the cluster ----------------------------------------------------------------------------

# Deleting one of these is not an action a sentence about a namespace fits,
# and the classifier refuses most of them to the coding session anyway.
_CLUSTER_SCOPED = frozenset({
    "namespace", "namespaces", "ns", "node", "nodes", "no", "pv", "persistentvolume",
    "persistentvolumes", "crd", "crds", "customresourcedefinition",
    "customresourcedefinitions", "clusterrole", "clusterroles", "clusterrolebinding",
    "clusterrolebindings", "storageclass", "storageclasses", "sc",
})


def _resources(words: list[str]) -> str:
    """`pod web` / `pod a b` / `pod/web deployment/api`, as spoken."""
    if not words:
        raise NotNameable("no resource")
    pairs: list[tuple[str, str]] = []
    if all("/" in word for word in words):
        for word in words:
            kind, _, resource = word.partition("/")
            pairs.append((kind, resource))
    elif not any("/" in word for word in words) and len(words) >= 2:
        pairs = [(words[0], resource) for resource in words[1:]]
    else:
        raise NotNameable("resources in a form the read-back cannot say")
    if len(pairs) > MAX_ITEMS:
        raise NotNameable("more resources than one sentence holds")
    spoken = []
    for kind, resource in pairs:
        if kind.lower() in _CLUSTER_SCOPED:
            raise NotNameable("a cluster-wide resource")
        spoken.append(f"{targets.label(kind).spoken} {targets.label(resource).spoken}")
    return join_spoken(spoken)


def _cluster_parts(values: dict, context_part: str, context_option: str):
    missing, options = [], []
    if "namespace" not in values:
        missing.append(PART_NAMESPACE)
        options += ["-n", "--namespace"]
    if "context" not in values:
        missing.append(context_part)
        options += [context_option]
    namespace = targets.label(values["namespace"]) if "namespace" in values else None
    context = targets.name(values["context"]) if "context" in values else None
    return missing, options, namespace, context


def _name_kubectl(subpath, args, rest, ctx, config) -> Named | Missing:
    valued = {"-n": "namespace", "--namespace": "namespace", "--context": "context"}
    if subpath == ("delete",):
        values, flags, positional = read_options(
            args, flags={"--force", "--wait", "--now", "--ignore-not-found"},
            valued={**valued, "--grace-period": "grace"},
        )
        if "grace" in values:
            targets.number(values["grace"])
        what = _resources(_after(positional, subpath))
        missing, options, namespace, context = _cluster_parts(values, PART_CONTEXT, "--context")
        if missing:
            return Missing(tuple(missing), tuple(options))
        verb = "Force delete" if "--force" in flags else "Delete"
        return Named(
            f"{verb} {what} in namespace {namespace.spoken} on cluster {context.spoken}.",
            frozenset({"delete", "force"} if "--force" in flags else {"delete"}),
        )
    if subpath == ("rollout", "restart"):
        values, _, positional = read_options(args, flags=set(), valued=valued)
        what = _resources(_after(positional, subpath))
        missing, options, namespace, context = _cluster_parts(values, PART_CONTEXT, "--context")
        if missing:
            return Missing(tuple(missing), tuple(options))
        return Named(
            f"Restart {what} in namespace {namespace.spoken} on cluster {context.spoken}.",
            frozenset({"deploy"}),
        )
    raise NotNameable("no sentence for this kubectl subcommand")


def _name_helm(subpath, args, rest, ctx, config) -> Named | Missing:
    valued = {"-n": "namespace", "--namespace": "namespace", "--kube-context": "context"}
    if subpath not in (("uninstall",), ("rollback",)):
        raise NotNameable("no sentence for this helm subcommand")
    values, _, positional = read_options(args, flags={"--wait", "--keep-history"}, valued=valued)
    words = _after(positional, subpath)
    if subpath == ("uninstall",):
        if len(words) != 1:
            raise NotNameable("exactly one release")
        release, tail = targets.label(words[0]), ""
        verb, cls = "Uninstall", "delete"
    else:
        if len(words) not in (1, 2):
            raise NotNameable("one release and at most a revision")
        release = targets.label(words[0])
        tail = f" to revision {targets.number(words[1]).spoken}" if len(words) == 2 else ""
        verb, cls = "Roll back", "deploy"
    missing, options, namespace, context = _cluster_parts(
        values, PART_KUBE_CONTEXT, "--kube-context")
    if missing:
        return Missing(tuple(missing), tuple(options))
    return Named(
        f"{verb} release {release.spoken}{tail} in namespace {namespace.spoken}"
        f" on cluster {context.spoken}.",
        frozenset({cls}),
    )


# --- git push -------------------------------------------------------------------------------

_PUSH_FLAGS = {
    "-u": "upstream", "--set-upstream": "upstream", "--no-verify": "no-verify",
    "-q": "quiet", "--quiet": "quiet",
    "-f": "force", "--force": "force", "--force-with-lease": "force",
    "-d": "delete", "--delete": "delete",
}


def push_remote(argv: tuple[str, ...]) -> str | None:
    """The remote a `git push ...` names, or None. For the hash."""
    if len(argv) < 2 or argv[1] != "push":
        return None
    return classify.parse_push(list(argv[2:])).remote


def _spoken_destination(destination: str) -> str:
    if destination.startswith("refs/heads/"):
        destination = destination[len("refs/heads/"):]
    elif destination.startswith("refs/tags/"):
        return "tag " + targets.ref(destination[len("refs/tags/"):]).spoken
    elif destination.startswith("refs/"):
        raise NotNameable("a destination that is neither a branch nor a tag")
    return targets.ref(destination).spoken


def _name_push(argv, ctx, config) -> Named | Missing:
    # `git push` with nothing between the two words: an option of git itself
    # (-C, -c, --git-dir) changes which repository and configuration apply.
    if len(argv) < 2 or argv[1] != "push":
        raise NotNameable("options of git itself before push")
    _, flags, positional = read_options(list(argv[2:]), flags=_PUSH_FLAGS, valued={})
    if len(positional) < 2:
        return Missing((PART_REMOTE,), (), words=True)
    remote, refspecs = positional[0], positional[1:]
    if len(refspecs) > MAX_ITEMS:
        raise NotNameable("more refs than one sentence holds")
    address = pushaddr.resolve_push(remote, ctx.cwd, config.forge_host, ctx.git)
    if address is None:
        raise NotNameable("the push address is not one repository on the forge host")
    repo = targets.repository(address.repository, config.own_owners)

    force = "force" in flags
    deletes: list[str] = []
    pushes: list[str] = []
    pairs: list[tuple[str, str]] = []
    for refspec in refspecs:
        if refspec.startswith("+"):
            force = True
            refspec = refspec[1:]
        if refspec == "tag":
            raise NotNameable("the tag form of a refspec")
        if "delete" in flags:
            if ":" in refspec:
                raise NotNameable("a refspec with a colon under --delete")
            deletes.append(_spoken_destination(refspec))
        elif refspec.startswith(":"):
            deletes.append(_spoken_destination(refspec[1:]))
        elif ":" in refspec:
            source, destination = refspec.split(":", 1)
            said_source = targets.ref(source).spoken
            said_destination = _spoken_destination(destination)
            pushes.append(said_source if said_source == said_destination
                          else f"{said_source} as {said_destination}")
            pairs.append((source, destination))
        else:
            pushes.append(_spoken_destination(refspec))
            pairs.append((refspec, refspec))
    if deletes and pushes:
        raise NotNameable("a push that both sends and deletes")
    if deletes:
        return Named(
            f"Delete {join_spoken(deletes)} in the repository {repo.spoken}.",
            frozenset({"delete", "force"} if force else {"delete"}),
        )
    verb = "Force push" if force else "Push"
    return Named(
        f"{verb} {join_spoken(pushes)} to the repository {repo.spoken}.",
        frozenset({"merge", "force"} if force else {"merge"}),
        status=("push", remote, tuple(pairs)),
    )


# --- copies, mail, host commands -----------------------------------------------------------

def _name_transfer(program, args, ctx, config) -> Named:
    hosts = classify.remote_hosts(program, args, ctx)
    if not hosts or len(hosts) > MAX_ITEMS:
        raise NotNameable("no host, or more hosts than one sentence holds")
    spoken = []
    for host in hosts:
        said = targets.host(host).spoken
        if said not in spoken:
            spoken.append(said)
    word = "host" if len(spoken) == 1 else "hosts"
    return Named(f"Copy files to or from {word} {join_spoken(spoken)}.", frozenset({"outward"}))


def _addresses(values: list[str]) -> list[str]:
    out = []
    for value in values:
        for piece in value.split(","):
            out.append(targets.mail_address(piece.strip()).spoken)
    return out


def _mail_sentence(to: list[str], cc: list[str], bcc: list[str]) -> str:
    if not to and not cc and not bcc:
        raise NotNameable("a mail without a recipient")
    if len(to) + len(cc) + len(bcc) > MAX_RECIPIENTS:
        raise NotNameable("more recipients than one sentence holds")
    parts = []
    if to:
        parts.append(f"to {join_spoken(to)}")
    if cc:
        parts.append(f"with a copy to {join_spoken(cc)}")
    if bcc:
        parts.append(f"with a blind copy to {join_spoken(bcc)}")
    return "Send a mail " + ", ".join(parts) + "."


def _name_mail(args, ctx, config) -> Named:
    to: list[str] = []
    cc: list[str] = []
    bcc: list[str] = []
    index = 0
    while index < len(args):
        word = args[index]
        index += 1
        if word in ("-s", "-c", "-b"):
            if index >= len(args):
                raise NotNameable(f"{word} without a value")
            value = args[index]
            index += 1
            if word == "-c":
                cc.append(value)
            elif word == "-b":
                bcc.append(value)
        elif word.startswith("-"):
            raise NotNameable(f"an option the read-back cannot say: {word}")
        else:
            to.append(word)
    return Named(_mail_sentence(_addresses(to), _addresses(cc), _addresses(bcc)),
                 frozenset({"message"}))


_KINDS = {
    "number": targets.number, "label": targets.label, "name": targets.name,
    "host": targets.host, "ref": targets.ref, "mail": targets.mail_address,
}


def _name_host_action(program, args, ctx, config) -> Named:
    """A host-local command with its own sentence (rules.d, D36): a fixed
    sentence with one placeholder per argument, each of a declared kind."""
    for action in ctx.rules.actions:
        if action.sentence is None or program not in action.programs:
            continue
        if any(arg.startswith("-") for arg in args):
            raise NotNameable("an option on a command with a fixed sentence")
        words = list(args)
        for path in action.paths or ((),):
            if tuple(words[:len(path)]) == path:
                rest = words[len(path):]
                if len(rest) != len(action.kinds):
                    continue
                spoken = [_KINDS[kind](value).spoken for kind, value in zip(action.kinds, rest)]
                return Named(action.sentence.format(*spoken), frozenset({action.cls}))
    raise NotNameable("no sentence for this command")


# --- the confirmation switch ------------------------------------------------------------------

def switch_sentence(argv: tuple[str, ...]) -> str:
    """The one templated read-back of the class `self` (D36): the fixed
    `confirm on|off [class]` command. `argv` has already been matched
    exactly by agent_host.classify."""
    if len(argv) not in (3, 4) or argv[2] not in ("on", "off"):
        raise NotNameable("not the fixed switch command")
    state = argv[2]
    if len(argv) == 4:
        if argv[3] not in CONFIRM_CLASSES:
            raise NotNameable("not a class")
        return f"Switch voice confirmation {state} for {argv[3]} actions."
    return f"Switch voice confirmation {state} for everything."


# --- one call ---------------------------------------------------------------------------------

def _name_inner(argv: tuple[str, ...], ctx, config) -> Named | Missing:
    program = classify.shell.program_name(argv[0])
    args = list(argv[1:])
    rules = ctx.rules
    if program == "git":
        return _name_push(argv, ctx, config)
    if program in rules.transfer_programs:
        return _name_transfer(program, args, ctx, config)
    if program in ("mail", "mailx", "s-nail"):
        return _name_mail(args, ctx, config)
    if program in rules.programs:
        subpath, placed, rest = classify.find_verb(rules.programs[program], args)
        if not placed or not subpath:
            raise NotNameable("the verb cannot be placed")
        namer = {"gh": _name_gh, "kubectl": _name_kubectl, "helm": _name_helm}.get(program)
        if namer is None:
            raise NotNameable("no sentence for this program")
        return namer(subpath, args, rest, ctx, config)
    return _name_host_action(program, args, ctx, config)


def _name_command(verdict: classify.Verdict, ctx, config) -> Named | Missing | None:
    command = verdict.command
    if command is None or not command.argv:
        return None
    roots = [name for kind, name in command.wrappers if kind == "root"]
    hosts = [name for kind, name in command.wrappers if kind == "ssh"]
    classes = set(verdict.classes)
    # `root` without a wrapper the gate unwrapped (a `sudo` somewhere in the
    # words) has no sentence. Neither has more than one of either wrapper.
    if ("root" in classes) != (len(roots) == 1) or len(roots) > 1 or len(hosts) > 1:
        return None
    inner_classes = classes - {"root"}
    if not inner_classes:
        return None  # `sudo systemctl ...`: the inner command must itself be nameable
    try:
        outcome = _name_inner(command.argv, ctx, config)
        if isinstance(outcome, Missing):
            return outcome
        if not inner_classes <= outcome.covers:
            return None
        sentence = outcome.sentence
        if hosts:
            sentence = f"{sentence[:-1]}, through host {targets.host(hosts[0].split('@')[-1]).spoken}."
        if roots:
            sentence = "As root: " + sentence
        return Named(sentence, frozenset(classes), outcome.status)
    except NotNameable:
        return None


def _name_tool(tool_name: str, tool_input: dict, ctx, config) -> Named | None:
    """A connector tool with a host-local naming rule (`[[tool_actions]]`)."""
    for rule in ctx.rules.tool_actions:
        if not fnmatch.fnmatchcase(tool_name, rule.tool):
            continue
        try:
            groups: list[list[str]] = []
            for field in rule.recipients:
                value = tool_input.get(field, [])
                if isinstance(value, str):
                    value = [value]
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise NotNameable("recipients that are not addresses")
                groups.append(_addresses(value))
            while len(groups) < 3:
                groups.append([])
            if len(groups) > 3:
                raise NotNameable("more recipient fields than the sentence has")
            return Named(_mail_sentence(*groups), frozenset({rule.cls}))
        except NotNameable:
            return None
    return None


def name_call(tool_name: str, tool_input: dict, verdict: classify.Verdict, ctx, config):
    if not verdict.gated or verdict.run_alone:
        return None
    if verdict.rule == classify.RULE_CONFIRM_SWITCH:
        try:
            return Named(switch_sentence(verdict.command.argv), frozenset({"self"}))
        except NotNameable:
            return None
    if verdict.desk_only:
        return None
    if tool_name == "Bash":
        return _name_command(verdict, ctx, config)
    return _name_tool(tool_name, tool_input, ctx, config)


def derive(tool_name: str, tool_input: dict, ctx: classify.Context,
           config: AgentHostConfig) -> Derived:
    """Everything about one call, from the call alone. See the module docstring."""
    verdict = classify.classify(tool_name, tool_input, ctx)
    cwd = str(ctx.cwd)
    base_hash = gatehash.call_hash(tool_name, cwd, tool_input)
    full_hash: str | None = base_hash
    if verdict.gated and verdict.rule.startswith("push:") and verdict.command is not None:
        remote = push_remote(verdict.command.argv)
        address = pushaddr.raw_push_address(remote, ctx.cwd, ctx.git) if remote else None
        full_hash = (
            gatehash.call_hash(tool_name, cwd, tool_input, address) if address is not None else None
        )
    outcome = name_call(tool_name, tool_input, verdict, ctx, config)
    if isinstance(outcome, Named) and full_hash is None:
        outcome = None
    return Derived(verdict=verdict, outcome=outcome, hash=full_hash, base_hash=base_hash)


def is_repair(refused_command: str, command: str, missing_options, words: bool) -> bool:
    """Is `command` the refused command plus only the parts that were asked for?

    The same words in the same order, with nothing changed or removed; what
    is added is an option from the list with its value, or (for a push) words
    at the end. Anything else is another call (D36).
    """
    try:
        old = shlex.split(refused_command)
        new = shlex.split(command)
    except ValueError:
        return False
    options = set(missing_options)
    added = False
    i = j = 0
    while i < len(new):
        word = new[i]
        if j < len(old) and word == old[j]:
            i += 1
            j += 1
        elif word in options and i + 1 < len(new):
            i += 2
            added = True
        elif word.startswith("--") and word.split("=", 1)[0] in options and "=" in word:
            i += 1
            added = True
        elif words and j == len(old) and not word.startswith("-"):
            i += 1
            added = True
        else:
            return False
    return added and j == len(old)
