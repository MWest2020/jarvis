"""The strict shape of a target per kind, the length cap and the
secret-shaped pattern (tasks 1.7c, 1.14; design D36)."""
import pytest

from agent_host import targets
from agent_host.targets import NotNameable

OWN = ("me",)
SHA = "3f9c2ab" + "0123456789abcdef0123456789abcdef0"
assert len(SHA) == 40


def _not(fn, *args, **kwargs):
    with pytest.raises(NotNameable):
        fn(*args, **kwargs)


# --- number ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["1", "21", "000123", "9" * 30])
def test_a_number_is_digits_only(value):
    assert targets.number(value).spoken == value


@pytest.mark.parametrize("value", [
    "", "21 and also delete", "twenty-one", "21a", "-21", "+21", "2.1", " 21", "21\n", "#21",
    "٢١", "9" * 101, 21, None,
])
def test_a_number_with_words_or_anything_else_is_not_nameable(value):
    _not(targets.number, value)


# --- repository -----------------------------------------------------------------------

def test_a_repository_is_spoken_as_name_or_as_name_of_owner():
    assert targets.repository("me/jarvis", OWN).spoken == "jarvis"
    assert targets.repository("ME/jarvis", OWN).spoken == "jarvis"
    assert targets.repository("them/jarvis", OWN).spoken == "jarvis of them"
    assert targets.repository("me/jarvis", ()).spoken == "jarvis of me"
    assert targets.repository("my-org/a.b_c-d", OWN).spoken == "a.b_c-d of my-org"


@pytest.mark.parametrize("value", [
    "", "jarvis", "me/", "/jarvis", "me/jarvis/extra", "me/jar vis", "me /jarvis",
    "me/jarvis\n", "github.com/me/jarvis", "https://github.com/me/jarvis", "me\\jarvis",
    "me/jarvis;rm", "me/$(x)", "../jarvis", "me/..", "me/" + "a" * 100, None,
])
def test_a_repository_with_a_space_or_another_shape_is_not_nameable(value):
    _not(targets.repository, value, OWN)


# --- ref --------------------------------------------------------------------------------

@pytest.mark.parametrize("value,spoken", [
    ("main", "main"),
    ("release/1.2", "release slash 1.2"),
    ("feature/voice/yes", "feature slash voice slash yes"),
    ("v1.0.0", "v1.0.0"),
    ("fix-123_b", "fix-123_b"),
    ("HEAD", "HEAD"),
])
def test_a_ref_is_spoken_with_slash_as_a_word(value, spoken):
    assert targets.ref(value).spoken == spoken


@pytest.mark.parametrize("value", [
    "", "ma in", "main\n", "main\nand more", "a\tb", "a..b", "a~1", "a^", "a:b", "a?b", "a*",
    "a[b", "a\\b", "-main", "/main", "main/", "main.", "a//b", "a/.b", ".a", "a.lock",
    "a.lock/b", "a@{b", "@", "a\x7fb", None,
])
def test_a_ref_git_would_not_accept_is_not_nameable(value):
    _not(targets.ref, value)


def test_a_long_hyphenated_branch_name_stays_nameable():
    branch = "fix-the-thing-that-broke-when-we-moved-the-gate-to-its-own-hook"
    assert 60 <= len(branch) <= 100
    assert targets.ref(branch).value == branch
    nested = "feature/" + "-".join(["word"] * 15)
    assert targets.ref(nested).spoken.startswith("feature slash word-word")


def test_a_ref_that_carries_a_token_is_not_nameable():
    token = "a1B2c3D4e5F6a1B2c3D4e5F6a1B2c3D4"
    assert len(token) == 32
    _not(targets.ref, f"feature/{token}")
    _not(targets.ref, f"fix-{token}-x")
    _not(targets.ref, f"x_{token}")
    _not(targets.ref, token)
    assert targets.ref(f"feature/{token[:31]}").value  # 31 characters: still a word
    _not(targets.ref, "sk-ant-abc")
    _not(targets.ref, "feature/sk-ant-x")


def test_the_length_cap_holds_for_the_whole_ref():
    ok = "/".join(["abcdefghi"] * 10)
    assert len(ok) == 99
    assert targets.ref(ok)
    assert targets.ref(ok + "x")  # 100
    _not(targets.ref, ok + "xy")  # 101


# --- commit ------------------------------------------------------------------------------

def test_a_commit_is_spoken_as_its_first_seven_hex_digits():
    target = targets.commit(SHA)
    assert target.spoken == "3f9c2ab"
    assert target.value == SHA  # the hash and the merge pin keep the full value
    assert targets.commit("3F9C2AB").spoken == "3f9c2ab"
    assert targets.commit(SHA, full=True).spoken == "3f9c2ab"


@pytest.mark.parametrize("value", ["", "3f9c2a", "3f9c2ag", SHA + "0", "HEAD", "main", " " + SHA, None])
def test_a_commit_that_is_not_7_to_40_hex_digits_is_not_nameable(value):
    _not(targets.commit, value)


def test_a_merge_pin_must_be_the_full_commit():
    _not(targets.commit, "3f9c2ab", full=True)
    _not(targets.commit, SHA[:39], full=True)


# --- mail address --------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["a@example.org", "first.last+tag@sub.example.co.uk"])
def test_a_mail_address_is_one_local_at_domain(value):
    assert targets.mail_address(value).spoken == value


@pytest.mark.parametrize("value", [
    "", "someone", "someone@", "@example.org", "someone@localhost", "a@b@example.org",
    "Some One <a@example.org>", "a@example.org,b@example.org", "a@example.org b@example.org",
    "a@exa mple.org", "a@-example.org", "a@example..org", "a b@example.org", "a@example.org\n",
    "x" * 95 + "@example.org", None,
])
def test_an_address_without_a_domain_or_with_more_in_it_is_not_nameable(value):
    _not(targets.mail_address, value)


# --- label, name, host -----------------------------------------------------------------------

@pytest.mark.parametrize("value", ["web", "web-1", "a", "kube-system", "x" * 31])
def test_a_dns_label(value):
    assert targets.label(value).spoken == value


@pytest.mark.parametrize("value", [
    "", "Web", "web_1", "-web", "web-", "web.example", "web 1", "web;x", "x" * 64, "wéb", None,
])
def test_what_is_not_a_dns_label_is_not_nameable(value):
    _not(targets.label, value)


@pytest.mark.parametrize("value", ["homelab", "admin@homelab", "gke_proj_zone_name", "ctx-1.prod"])
def test_a_context_name(value):
    assert targets.name(value).spoken == value


@pytest.mark.parametrize("value", ["", "home lab", "-x", "x-", "a/b", "a;b", "a$b", "x" * 101, None])
def test_what_is_not_a_plain_name_is_not_nameable(value):
    _not(targets.name, value)


@pytest.mark.parametrize("value", [
    "web", "web.example.org", "WEB.example.org", "10.0.0.5", "2001:db8::1", "[2001:db8::1]",
])
def test_a_host_is_a_dns_name_or_an_address(value):
    assert targets.host(value).spoken == value


@pytest.mark.parametrize("value", [
    "", "web host", "web_host", "-web", "web..example", "user@web", "web:22", "web/path",
    "https://web", "999.1.1.1.1.", "x" * 64 + ".org", None,
])
def test_what_is_not_a_host_is_not_nameable(value):
    _not(targets.host, value)


# --- path --------------------------------------------------------------------------------------

@pytest.fixture
def short_tmp():
    # pytest's own tmp_path has a directory name of more than 32 token
    # characters, which is (rightly) shaped like a secret.
    import shutil
    import tempfile
    from pathlib import Path
    path = Path(tempfile.mkdtemp(prefix="jv-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_a_path_must_really_lie_under_an_allowed_root(short_tmp, tmp_path):
    tmp_path = short_tmp
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    assert targets.path(str(root / "sub" / "file"), [root]).spoken == str(root / "sub" / "file")
    assert targets.path(str(root), [root])
    _not(targets.path, str(outside / "file"), [root])
    _not(targets.path, str(root / ".." / "outside" / "file"), [root])
    _not(targets.path, str(tmp_path / "rootless"), [root])  # a prefix of the name is not under it
    _not(targets.path, "relative/file", [root])
    _not(targets.path, str(root / "x"), [])
    # A symlink that resolves outside its root.
    (root / "link").symlink_to(outside)
    _not(targets.path, str(root / "link" / "file"), [root])
    # Over the cap, or shaped like a secret.
    _not(targets.path, str(root / ("d/" * 60) / "f"), [root])
    _not(targets.path, str(root / ("a" * 32)), [root])


# --- the cap and the secret-shaped pattern, for every kind ------------------------------------

def test_a_target_of_101_characters_is_not_nameable():
    assert targets.name("a" * 31 + "." + "b" * 31 + "." + "c" * 31)  # 95, no long run
    for value, fn in (
        ("1" * 101, targets.number),
        ("o/" + ".".join(["abcdefghi"] * 10), lambda v: targets.repository(v, OWN)),
        (".".join(["abcdefghi"] * 10) + "xy", targets.name),
        (".".join(["abcdefghi"] * 10) + ".xy", targets.host),
    ):
        assert len(value) > 100
        _not(fn, value)


def test_a_target_of_32_token_characters_is_not_nameable():
    token = "a1B2c3D4e5F6a1B2c3D4e5F6a1B2c3D4"
    _not(targets.repository, f"me/{token}", OWN)
    _not(targets.repository, f"{token}/x", OWN)
    _not(targets.name, token)
    _not(targets.host, token + ".example.org")
    _not(targets.number, "1" * 32)
    _not(targets.repository, "me/sk-ant-x", OWN)
    _not(targets.name, "sk-ant-abc")
    _not(targets.mail_address, "sk-ant-abc@example.org")
    # 31 characters are a long name, not a secret.
    assert targets.name(token[:31])
    assert targets.repository(f"me/{token[:28]}", OWN)


def test_a_full_commit_id_is_not_a_secret_because_it_is_spoken_as_seven_digits():
    assert targets.commit(SHA, full=True).spoken == "3f9c2ab"


def test_join_spoken():
    assert targets.join_spoken([]) == ""
    assert targets.join_spoken(["a"]) == "a"
    assert targets.join_spoken(["a", "b"]) == "a and b"
    assert targets.join_spoken(["a", "b", "c"]) == "a, b and c"


# --- the second review of #44: plain ASCII only -----------------------------------------------

@pytest.mark.parametrize("fn,value", [
    (lambda v: targets.repository(v, OWN), "me/jаrvis"),            # a Cyrillic а
    (lambda v: targets.repository(v, OWN), "mе/jarvis"),            # a Cyrillic е in the owner
    (targets.ref, "mаin"),
    (targets.ref, "feature/\u202emain"),                             # a direction override
    (targets.ref, "main\u200b"),                                     # a zero-width space
    (targets.ref, "main\u00a0x"),                                    # a no-break space
    (targets.mail_address, "a@exаmple.org"),
    (targets.mail_address, "а@example.org"),
    (targets.mail_address, "a@example.org\u200f"),
    (targets.host, "exаmple.org"),
    (targets.host, "ｗｅｂ.example.org"),                               # full-width letters
    (targets.host, "web\u3002example\u3002org"),                     # an ideographic full stop
    (targets.host, "fe80::1%eth0"),                                  # a zone id
    (targets.host, "fe80::1%25eth0"),
    (targets.host, "[fe80::1%evil]"),
    (targets.label, "wеb"),
    (targets.name, "homelаb"),
    (targets.number, "２１"),                                         # full-width digits
    (targets.number, "٢١"),
    (targets.commit, "３f9c2ab"),
])
def test_a_target_outside_plain_ascii_is_not_nameable(fn, value):
    _not(fn, value)


def test_the_secret_pattern_is_ascii_too():
    _not(targets.name, "SK-ANT-abc")
    _not(targets.name, "sk-ant-abc")
    _not(targets.ref, "feature/SK-ANT-x")
    assert targets.host("WEB.Example.ORG").spoken == "WEB.Example.ORG"
    assert targets.host("2001:db8::1") and targets.host("[2001:db8::1]") and targets.host("10.0.0.5")
