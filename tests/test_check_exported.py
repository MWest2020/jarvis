"""The CI check on exported Android components (android/ci/check_exported.py,
jarvis#16), fed with dumps in the format of `aapt2 dump xmltree`.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_exported", ROOT / "android" / "ci" / "check_exported.py")
check_exported = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_exported)

NS = "http://schemas.android.com/apk/res/android"

HEAD = f"""N: android={NS} (line=2)
  E: manifest (line=2)
    A: {NS}:versionCode(0x0101021b)=1
    A: package="io.github.mwest2020.jarvis" (Raw: "io.github.mwest2020.jarvis")
      E: uses-permission (line=9)
        A: {NS}:name(0x01010003)="android.permission.INTERNET" (Raw: "android.permission.INTERNET")
      E: application (line=20)
        A: {NS}:label(0x01010001)=@0x7f0b0000
        A: {NS}:name(0x01010003)="androidx.core.app.CoreComponentFactory" (Raw: "androidx.core.app.CoreComponentFactory")
"""

MAIN = f"""        E: activity (line=30)
          A: {NS}:name(0x01010003)="io.github.mwest2020.jarvis.MainActivity" (Raw: "io.github.mwest2020.jarvis.MainActivity")
          A: {NS}:exported(0x01010010)=true
          A: {NS}:windowSoftInputMode(0x0101022b)=0x10
            E: intent-filter (line=34)
              E: action (line=35)
                A: {NS}:name(0x01010003)="android.intent.action.MAIN" (Raw: "android.intent.action.MAIN")
              E: category (line=36)
                A: {NS}:name(0x01010003)="android.intent.category.LAUNCHER" (Raw: "android.intent.category.LAUNCHER")
"""

PROVIDER = f"""        E: provider (line=40)
          A: {NS}:name(0x01010003)="androidx.startup.InitializationProvider" (Raw: "androidx.startup.InitializationProvider")
          A: {NS}:exported(0x01010010)=false
          A: {NS}:authorities(0x01010018)="io.github.mwest2020.jarvis.androidx-startup" (Raw: "io.github.mwest2020.jarvis.androidx-startup")
            E: meta-data (line=44)
              A: {NS}:name(0x01010003)="androidx.profileinstaller.ProfileInstallerInitializer" (Raw: "androidx.profileinstaller.ProfileInstallerInitializer")
              A: {NS}:value(0x01010024)="androidx.startup" (Raw: "androidx.startup")
"""


def receiver(permission='"android.permission.DUMP" (Raw: "android.permission.DUMP")'):
    permission_line = f"          A: {NS}:permission(0x01010006)={permission}\n" if permission else ""
    return f"""        E: receiver (line=50)
          A: {NS}:name(0x01010003)="androidx.profileinstaller.ProfileInstallReceiver" (Raw: "androidx.profileinstaller.ProfileInstallReceiver")
{permission_line}          A: {NS}:enabled(0x0101000e)=true
          A: {NS}:exported(0x01010010)=true
          A: {NS}:directBootAware(0x01010505)=false
            E: intent-filter (line=56)
              E: action (line=57)
                A: {NS}:name(0x01010003)="androidx.profileinstaller.action.INSTALL_PROFILE" (Raw: "androidx.profileinstaller.action.INSTALL_PROFILE")
"""


def component(tag, name, *lines, intent_filter=False):
    text = f"        E: {tag} (line=70)\n"
    text += f'          A: {NS}:name(0x01010003)="{name}" (Raw: "{name}")\n'
    for line in lines:
        text += f"          A: {NS}:{line}\n"
    if intent_filter:
        text += ("            E: intent-filter (line=72)\n"
                 "              E: action (line=73)\n"
                 f'                A: {NS}:name(0x01010003)="x.ACTION" (Raw: "x.ACTION")\n')
    return text


def test_the_expected_manifest_passes():
    assert check_exported.problems(HEAD + MAIN + PROVIDER + receiver()) == []


def test_the_child_elements_do_not_overwrite_a_components_name():
    found = check_exported.components(HEAD + MAIN + PROVIDER + receiver())
    assert [c["name"] for c in found] == [
        "io.github.mwest2020.jarvis.MainActivity",
        "androidx.startup.InitializationProvider",
        "androidx.profileinstaller.ProfileInstallReceiver",
    ]
    assert [c["filtered"] for c in found] == [True, False, True]


def test_an_extra_exported_service_fails():
    extra = component("service", "com.example.SyncService", "exported(0x01010010)=true")
    found = check_exported.problems(HEAD + MAIN + extra)
    assert len(found) == 1 and "com.example.SyncService" in found[0]


def test_an_extra_component_that_is_not_exported_passes():
    extra = component("service", "com.example.SyncService", "exported(0x01010010)=false")
    assert check_exported.problems(HEAD + MAIN + extra) == []


def test_other_spellings_of_exported():
    for value, exported in [("true", True), ("false", False), ("(type 0x12)0xffffffff", True),
                            ("(type 0x12)0x0", False), ('"true" (Raw: "true")', True),
                            ('"false" (Raw: "false")', False), ("@0x7f050000", True)]:
        extra = component("provider", "com.example.Files", f"exported(0x01010010)={value}")
        assert bool(check_exported.problems(HEAD + MAIN + extra)) is exported, value


def test_no_exported_attribute_counts_as_exported_with_an_intent_filter():
    quiet = component("receiver", "com.example.Quiet")
    assert check_exported.problems(HEAD + MAIN + quiet) == []
    listening = component("receiver", "com.example.Boot", intent_filter=True)
    assert len(check_exported.problems(HEAD + MAIN + listening)) == 1


def test_the_profile_receiver_needs_its_dump_guard():
    assert len(check_exported.problems(HEAD + MAIN + receiver(permission=None))) == 1
    other = '"android.permission.INTERNET" (Raw: "android.permission.INTERNET")'
    assert len(check_exported.problems(HEAD + MAIN + receiver(permission=other))) == 1


def test_the_launcher_must_not_ask_for_a_permission_it_never_had():
    guarded = MAIN.replace(
        f"          A: {NS}:exported(0x01010010)=true\n",
        f"          A: {NS}:exported(0x01010010)=true\n"
        f'          A: {NS}:permission(0x01010006)="x.P" (Raw: "x.P")\n')
    assert len(check_exported.problems(HEAD + guarded)) == 1


def test_an_exported_activity_alias_fails():
    alias = component("activity-alias", "io.github.mwest2020.jarvis.Share",
                      "exported(0x01010010)=true", intent_filter=True)
    assert len(check_exported.problems(HEAD + MAIN + alias)) == 1


def test_an_unreadable_dump_fails_instead_of_passing():
    assert check_exported.problems("") != []
    assert check_exported.problems("not a manifest dump\n") != []
    assert check_exported.problems(HEAD + PROVIDER) != []   # no launcher found


def test_a_relative_name_is_resolved_against_the_package():
    relative = MAIN.replace("io.github.mwest2020.jarvis.MainActivity", ".MainActivity")
    assert check_exported.problems(HEAD + relative) == []
    extra = component("service", ".Hidden", "exported(0x01010010)=true")
    assert len(check_exported.problems(HEAD + MAIN + extra)) == 1
