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
    # ("push", remote, ((source, destination), ...)) or
    # ("create", "owner/name") for `gh repo create`.
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
        if flags & {"--clone", "-c"}:
            # It would clone into the turn's checkout, a repository inside a
            # repository. The wrapper sets it up as a project instead (D53).
            raise NotNameable("a repository creation with --clone")
        if len(words) != 1:
            raise NotNameable("a repository creation names exactly one repository")
        repo = targets.repository(words[0], config.own_owners)
        visibility = flags & {"--public", "--private", "--internal"}
        if len(visibility) > 1:
            raise NotNameable("more than one visibility")
        # When the call does not say, it is read back as public (D36).
        word = visibility.pop()[2:] if visibility else "public"
        # The repository rides along for what the wrapper does after the
        # creation ran: clone it as a project (jarvis-general-conversation D53).
        return Named(f"Create the {word} repository {repo.spoken}.", frozenset({"create"}),
                     status=("create", repo.value))

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
        # The text is not a target and is not spoken, but only the options
        # that carry text are allowed: anything else (`--head`, `--base`,
        # `--assignee`, `--web`, `--edit-last` ...) changes what the call
        # does in a way the sentence would not say.
        text_options = {"--body": "body", "-b": "body", "--body-file": "body", "-F": "body"}
        if subpath[1] == "create":
            text_options.update({"--title": "title", "-t": "title"})
        review_flags = {"--comment": "comment", "-c": "comment", "--approve": "approve",
                        "-a": "approve", "--request-changes": "changes", "-r": "changes"}
        values, flags, positional = read_options(
            args, flags=review_flags if subpath == ("pr", "review") else set(),
            valued={**_REPO_OPTIONS, **text_options})
        words = _after(positional, subpath)
        number = None
        if subpath[1] != "create":
            if len(words) != 1:
                raise NotNameable("exactly one number")
            number = targets.number(words[0])
        elif words:
            raise NotNameable("an argument the sentence cannot say")
        sentence = message_forms[subpath]
        if subpath == ("pr", "review"):
            if len(flags) != 1:
                raise NotNameable("a review says exactly one of comment, approve, request changes")
            sentence = {
                "comment": sentence,
                "approve": "Approve pull request {n} on {repo}.",
                "changes": "Request changes on pull request {n} on {repo}.",
            }[next(iter(flags))]
        if "repo" not in values:
            return Missing((PART_REPO,), ("--repo", "-R"))
        repo = _checked_repo(values["repo"], ctx, config)
        return Named(
            sentence.format(n=number.spoken if number else "", repo=repo.spoken),
            frozenset({"message"}),
        )

    raise NotNameable("no sentence for this gh subcommand")


# --- the cluster ----------------------------------------------------------------------------

# The kinds the "in namespace X" sentence is true for. An allow-list: a
# kind that is not on it may be cluster-wide (a namespace, a node, a volume,
# a resource definition, a custom kind nobody knows), and a sentence that
# says "in namespace" about it would be wrong. Such a call needs the desk.
_NAMESPACED = frozenset({
    "pod", "pods", "po", "deployment", "deployments", "deploy", "statefulset",
    "statefulsets", "sts", "daemonset", "daemonsets", "ds", "replicaset", "replicasets",
    "rs", "job", "jobs", "cronjob", "cronjobs", "cj", "service", "services", "svc",
    "configmap", "configmaps", "cm", "secret", "secrets", "ingress", "ingresses", "ing",
    "persistentvolumeclaim", "persistentvolumeclaims", "pvc", "serviceaccount",
    "serviceaccounts", "sa", "horizontalpodautoscaler", "hpa", "networkpolicy",
    "networkpolicies", "netpol", "role", "roles", "rolebinding", "rolebindings",
    "endpoints", "ep",
})


# Kinds that hold data: deleting one is never confirmed by voice (host
# check 11, decided 2026-11-16). A volume claim, a volume, a namespace with
# everything in it, a resource definition with every object of its kind, a
# stateful set (and with it, often, its claims). The cluster-wide ones are
# not in _NAMESPACED either; they are named here so that the rule says what
# it means. Every form that deletes more than the named objects (`--all`,
# `-l`/`--selector`, `-f`/`--filename`, `-k`) is not nameable already:
# read_options knows none of those options.
DESK_ONLY_DELETE_KINDS = frozenset({
    "persistentvolumeclaim", "persistentvolumeclaims", "pvc",
    "persistentvolume", "persistentvolumes", "pv",
    "namespace", "namespaces", "ns",
    "customresourcedefinition", "customresourcedefinitions", "crd", "crds",
    "statefulset", "statefulsets", "sts",
})


def _resources(words: list[str], *, refuse: frozenset[str] = frozenset()) -> str:
    """`pod web` / `pod a b` / `pod/web deployment/api`, as spoken. A kind in
    `refuse` makes the call not nameable."""
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
        if kind.lower() in refuse or kind.split(".", 1)[0].lower() in refuse:
            raise NotNameable("a kind that holds data: deleting it needs the desk")
        if kind not in _NAMESPACED:
            raise NotNameable("a kind that is not known to live in a namespace")
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
        what = _resources(_after(positional, subpath), refuse=DESK_ONLY_DELETE_KINDS)
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


def resolve_push_refs(argv: tuple[str, ...], ctx) -> tuple[tuple[str, str], ...] | None:
    """What git will really push, per refspec, as (source, destination).

    `HEAD` and `@` are the current branch, read from the checkout: the name
    that is spoken and hashed is the branch, not the word. A refspec without
    a colon lands on the same name (the gate has already refused the call
    when the configuration maps it elsewhere). An empty source is a delete.
    None when something cannot be resolved; such a push is not nameable.

    Both the gate and the wrapper resolve through here, and the result is
    part of the push's hash: if the current branch changes between the
    read-back and the release, the hash no longer matches.
    """
    if len(argv) < 2 or argv[1] != "push":
        return None
    push = classify.parse_push(list(argv[2:]))
    current: str | None = None
    pairs: list[tuple[str, str]] = []
    for refspec in push.refspecs:
        refspec = refspec.lstrip("+")
        if refspec == "tag":
            return None
        source, colon, destination = refspec.partition(":")
        if not colon:
            destination = source
        if source in ("HEAD", "@") or destination in ("HEAD", "@"):
            if current is None:
                current = ctx.git(["symbolic-ref", "--short", "HEAD"], ctx.cwd)
                if not current:
                    return None
            source = current if source in ("HEAD", "@") else source
            destination = current if destination in ("HEAD", "@") else destination
        if not destination:
            return None
        pairs.append((source, destination))
    return tuple(pairs)


def _spoken_destination(destination: str) -> str:
    if destination.startswith("refs/heads/"):
        destination = destination[len("refs/heads/"):]
    elif destination.startswith("heads/"):
        destination = destination[len("heads/"):]
    elif destination.startswith("refs/tags/"):
        return "tag " + targets.ref(destination[len("refs/tags/"):]).spoken
    elif destination.startswith("refs/"):
        raise NotNameable("a destination that is neither a branch nor a tag")
    return targets.ref(destination).spoken


def _name_push(command, ctx, config) -> Named | Missing:
    argv = command.argv
    # `git push` with nothing between the two words: an option of git itself
    # (-C, -c, --git-dir) changes which repository and configuration apply.
    if len(argv) < 2 or argv[1] != "push":
        raise NotNameable("options of git itself before push")
    # Behind ssh, sudo or `bash -c` the push runs as somebody else or
    # somewhere else: the address resolved here would not be the address used.
    if command.wrappers:
        raise NotNameable("a push behind a wrapper")
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
    resolved = resolve_push_refs(argv, ctx)
    if resolved is None or len(resolved) != len(refspecs):
        raise NotNameable("a ref that cannot be resolved")

    force = "force" in flags
    deletes: list[str] = []
    pushes: list[str] = []
    pairs: list[tuple[str, str]] = []
    for refspec, (source, destination) in zip(refspecs, resolved):
        if refspec.startswith("+"):
            force = True
        if "delete" in flags:
            if ":" in refspec:
                raise NotNameable("a refspec with a colon under --delete")
            deletes.append(_spoken_destination(destination))
        elif not source:
            deletes.append(_spoken_destination(destination))
        else:
            said_source = _spoken_destination(source) if not source.startswith("refs/tags/") \
                else targets.ref(source).spoken
            said_destination = _spoken_destination(destination)
            pushes.append(said_source if said_source == said_destination
                          else f"{said_source} as {said_destination}")
            pairs.append((source, destination))
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
    # An option that changes which host is reached, through what, or what
    # runs there: the host in the sentence would not be the whole truth.
    # Short options are read letter by letter (`rsync -ave ssh`).
    if classify.transfer_reach(program, args):
        raise NotNameable("an option that changes how the host is reached")
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

def switch_sentence(argv: tuple[str, ...], confirm: str = "on") -> str:
    """The one templated read-back of the class `self` (D36): the fixed
    `confirm on|off [class]` command. `argv` has already been matched
    exactly by agent_host.classify."""
    if len(argv) not in (3, 4) or argv[2] not in ("on", "off"):
        raise NotNameable("not the fixed switch command")
    state = argv[2]
    if len(argv) == 4:
        if argv[3] not in CONFIRM_CLASSES:
            raise NotNameable("not a class")
        if state == "on" and confirm == "off":
            # Confirmation is off altogether: `on <class>` switches it on
            # with every other class skipped (agent_host.switch), and the
            # sentence says what that leaves.
            return (f"Switch voice confirmation on for {argv[3]} actions only."
                    " Everything else stays unconfirmed.")
        return f"Switch voice confirmation {state} for {argv[3]} actions."
    return f"Switch voice confirmation {state} for everything."


# --- one call ---------------------------------------------------------------------------------

def _name_inner(command, ctx, config) -> Named | Missing:
    argv = command.argv
    program = classify.shell.program_name(argv[0])
    args = list(argv[1:])
    rules = ctx.rules
    if program == "git":
        return _name_push(command, ctx, config)
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
    if verdict.rule in classify.PUSH_NOT_NAMEABLE:
        # The configuration maps the push elsewhere, or it runs for another
        # repository: the branch in the call is not where it lands.
        return None
    if command.env or command.odd_wrapper:
        # A variable set in front of the command (`GH_HOST=...`,
        # `KUBECONFIG=...`, `GIT_SSH_COMMAND=...`), a directory changed by a
        # wrapper (`env -C`), another user (`sudo -u`, `-g`, `-i`, `-D`), or
        # an ssh option that changes which host is reached and how (`-o`,
        # `-F`, `-J`, `-p`): each can change what every target in the
        # sentence means. Such a call is not read back.
        return None
    roots = [name for kind, name in command.wrappers if kind == "root"]
    hosts = [name for kind, name in command.wrappers if kind == "ssh"]
    classes = set(verdict.classes)
    # `root` without a wrapper the gate unwrapped (a `sudo` somewhere in the
    # words) has no sentence. Neither has more than one of either wrapper.
    if ("root" in classes) != (len(roots) == 1) or len(roots) > 1 or len(hosts) > 1:
        return None
    inner_classes = classes - {"root"}
    try:
        outcome = _name_inner(command, ctx, config)
        if isinstance(outcome, Missing):
            # Also for a call that is gated without a class yet (a bare
            # `git push` nobody can place): say what to write out.
            return outcome
        # `sudo systemctl ...`: the inner command must itself be nameable.
        if not inner_classes or not inner_classes <= outcome.covers:
            return None
        sentence = outcome.sentence
        if hosts:
            sentence = f"{sentence[:-1]}, through host {targets.host(hosts[0].split('@')[-1]).spoken}."
        if roots:
            sentence = "As root: " + sentence
        return Named(sentence, frozenset(classes), outcome.status)
    except NotNameable:
        return None


def is_mail_tool(tool_name: str) -> bool:
    """A Gmail connector tool, under any connector name (jarvis-mail-the-driver):
    never exempt from confirmation, and named by [own_mail] or not at all."""
    return tool_name.startswith("mcp__") and "gmail" in tool_name.lower()


def _name_own_mail(tool_input: dict, ctx, config, verdict_classes=()) -> Named | None:
    """A mail tool of [own_mail] (jarvis-mail-the-driver): nameable only as one
    mail to one of the user's own addresses alone. Anything else in the call
    (another recipient, a cc or bcc, a draft, an attachment, reply-all, a field
    nobody listed) leaves it without a sentence, so it needs the desk. Nothing
    of the subject or body is spoken (D36: no text from the call)."""
    rules = ctx.rules
    if not config.own_mail or not isinstance(tool_input, dict):
        return None
    if set(tool_input) - rules.own_mail_fields:
        return None
    if any(tool_input.get(field) not in (None, [], "") for field in rules.own_mail_empty_fields):
        return None
    if any(tool_input.get(field) not in (None, False) for field in rules.own_mail_off_fields):
        return None
    to = tool_input.get("to")
    if isinstance(to, str):
        to = [to]
    if not isinstance(to, list) or len(to) != 1 or not isinstance(to[0], str):
        return None
    raw = to[0]
    # The raw value, as it will run: plain ASCII with nothing around it, so the
    # read-back cannot differ from the bytes sent (a Kelvin sign lowers to "k",
    # an em space strips away). Only then is the case ignored.
    if not raw.isascii() or raw != raw.strip():
        return None
    try:
        spoken = targets.mail_address(raw).spoken
    except NotNameable:
        return None
    if raw.lower() not in config.own_mail:
        return None
    return Named(_mail_sentence([spoken], [], []), frozenset({"message"}) | set(verdict_classes))


def _name_tool(tool_name: str, tool_input: dict, ctx, config, verdict_classes=()) -> Named | None:
    """A connector tool with a naming rule: [own_mail] for the mail tools it
    lists (and only that rule); no rule at all for any other Gmail tool; else a
    host-local `[[tool_actions]]`."""
    if tool_name in ctx.rules.own_mail_tools:
        return _name_own_mail(tool_input, ctx, config, verdict_classes)
    if is_mail_tool(tool_name):
        return None     # reply, forward, a draft ...: never by voice, whatever rules.d says
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
            return Named(_mail_sentence(*groups), frozenset({rule.cls}) | set(verdict_classes))
        except NotNameable:
            return None
    return None


def name_call(tool_name: str, tool_input: dict, verdict: classify.Verdict, ctx, config):
    if not verdict.gated or verdict.run_alone:
        return None
    if verdict.rule == classify.RULE_CONFIRM_SWITCH:
        try:
            return Named(switch_sentence(verdict.command.argv, config.confirm),
                         frozenset({"self"}))
        except NotNameable:
            return None
    if verdict.desk_only:
        return None
    if tool_name == "Bash":
        return _name_command(verdict, ctx, config)
    return _name_tool(tool_name, tool_input, ctx, config, verdict.classes)


CREATE_WITH_CLONE_HINT = (
    "[create_clone] A repository is not created with --clone by voice: it would be"
    " cloned into this checkout, a repository inside a repository. Run the same gh repo"
    " create once more without --clone (and without -c), as one call. It is then read"
    " back to the driver, and after it ran Jarvis sets the new repository up as a"
    " project itself. Nothing was run."
)


def desk_hint(verdict: classify.Verdict) -> str | None:
    """The whole refusal for a call that can be asked for again in a form that
    can be read back, or None. Only for `gh repo create --clone`
    (jarvis-general-conversation D53)."""
    command = verdict.command
    if command is None or not command.argv or command.argv[0] != "gh":
        return None
    argv = list(command.argv)
    if argv[1:3] != ["repo", "create"]:
        return None
    if any(word in ("--clone", "-c") or word.startswith("--clone=") for word in argv[3:]):
        return CREATE_WITH_CLONE_HINT
    return None


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
        refs = resolve_push_refs(verdict.command.argv, ctx)
        full_hash = None
        if address is not None and refs is not None:
            # The address and every (source, destination) as resolved now.
            resolved = address + "".join(f"\0{source}:{destination}" for source, destination in refs)
            full_hash = gatehash.call_hash(tool_name, cwd, tool_input, resolved)
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
