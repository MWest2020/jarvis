#!/usr/bin/env python3
"""Fails when the APK exposes a component nobody decided to expose (jarvis#16).

Reads `aapt2 dump xmltree --file AndroidManifest.xml <apk>` on stdin: the merged
manifest as it ships, libraries included. A component (activity, activity-alias,
service, receiver, provider) may be exported only when it is in ALLOWED, with
exactly the permission written there. Everything else must say
android:exported="false", or say nothing and have no intent filter.

The check fails closed: a value of `exported` it cannot read counts as exported, a
component without a name is refused, and so is a manifest in which the launcher
activity is not found (the dump format changed, or this is another app).

To expose a component on purpose, add it to ALLOWED in the same pull request and
say why in docs/how-to/android-app.md.
"""
from __future__ import annotations

import re
import sys

LAUNCHER = "io.github.mwest2020.jarvis.MainActivity"
# name -> the permission a caller must hold (None: none, anyone may start it).
ALLOWED: dict[str, str | None] = {
    # The launcher icon starts it.
    LAUNCHER: None,
    # androidx.profileinstaller (comes with Compose): lets `adb shell` install the
    # baseline profile. Only a holder of DUMP (the shell, not an app) can send to it.
    "androidx.profileinstaller.ProfileInstallReceiver": "android.permission.DUMP",
}

COMPONENTS = {"activity", "activity-alias", "service", "receiver", "provider"}
_ELEMENT = re.compile(r"^( *)E: (\S+)")
_ATTRIBUTE = re.compile(
    r"^ *A: (?:http://schemas\.android\.com/apk/res/android:|android:)"
    r"(name|exported|permission)(?:\(0x[0-9a-fA-F]+\))?=(.*)$")
_FALSE = {"false", "0", "0x0", "(type 0x12)0x0"}


def _value(raw: str) -> str:
    raw = raw.strip()
    quoted = re.match(r'^"([^"]*)"', raw)
    if quoted:
        return quoted.group(1)
    return re.sub(r" \(Raw: .*$", "", raw)


def components(xmltree: str) -> list[dict]:
    """Every component in the dump: tag, name, exported (None when absent), permission, filtered."""
    found: list[dict] = []
    stack: list[tuple[int, str, dict | None]] = []   # (indent, tag, the component it is)
    for line in xmltree.splitlines():
        element = _ELEMENT.match(line)
        if element:
            indent, tag = len(element.group(1)), element.group(2)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            component = None
            if tag in COMPONENTS:
                component = {"tag": tag, "name": None, "exported": None, "permission": None,
                             "filtered": False}
                found.append(component)
            elif tag == "intent-filter" and stack and stack[-1][2] is not None:
                stack[-1][2]["filtered"] = True
            stack.append((indent, tag, component))
            continue
        attribute = _ATTRIBUTE.match(line)
        if attribute and stack and stack[-1][2] is not None:
            stack[-1][2][attribute.group(1)] = _value(attribute.group(2))
    return found


def is_exported(component: dict) -> bool:
    value = component["exported"]
    if value is None:
        # No attribute: Android's old default exports a component with an intent filter.
        return component["filtered"]
    return value.lower() not in _FALSE


def problems(xmltree: str, package: str = "io.github.mwest2020.jarvis") -> list[str]:
    found = components(xmltree)
    out: list[str] = []
    names = set()
    for component in found:
        name = component["name"]
        if not name:
            out.append(f"a {component['tag']} without a readable android:name")
            continue
        if name.startswith("."):
            name = package + name
        names.add(name)
        if not is_exported(component):
            continue
        if name not in ALLOWED:
            out.append(f"{component['tag']} {name} is exported and not on the list")
        elif component["permission"] != ALLOWED[name]:
            out.append(f"{component['tag']} {name} is exported with permission "
                       f"{component['permission']!r}, expected {ALLOWED[name]!r}")
    if LAUNCHER not in names:
        out.append(f"{LAUNCHER} was not found: the dump could not be read, so nothing was checked")
    return out


def main() -> int:
    found = problems(sys.stdin.read())
    for problem in found:
        print(f"Unexpected exported component: {problem}")
    if found:
        print("See android/ci/check_exported.py for the list and how to change it.")
        return 1
    print("Exported components: only " + ", ".join(sorted(ALLOWED)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
