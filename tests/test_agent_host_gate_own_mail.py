"""Mail to the driver's own address (change jarvis-mail-the-driver): a connector
send to `own_mail` alone is read back, with its subject framed and cleaned (Mark,
2026-11-24, #92), and can be confirmed by voice; any other
mail, a copy, a draft, an attachment, the reply tool or a forward needs the desk,
and no Gmail tool is ever let through by switching confirmation off.
Nothing is sent: the gate only judges the call."""
import unicodedata

import pytest

from agent_host import classify, decision, naming, wrapper
from bridge.remote_adapter import speakable_readback
from agent_host.config import ConfigError
from agent_host.naming import Named
from agent_host.rules import RulesError, load_rules
from tests.gatekit import Turn, derive, make_config, make_ctx, no_git

OWN = "driver@example.org"
SEND = "mcp__claude_ai_Gmail__send_message"
REPLY = "mcp__claude_ai_Gmail__reply"
G = "mcp__claude_ai_Gmail__"


@pytest.fixture
def env(tmp_path):
    config = make_config(tmp_path, own_mail=[OWN])
    return make_ctx(tmp_path, config=config), config


def outcome(env, tool, **tool_input):
    ctx, config = env
    derived = derive("", ctx, config, tool=tool, tool_input=tool_input)
    assert derived.verdict.gated, tool
    return derived.outcome


NO_SUBJECT = f"Send a mail to {OWN}, without a subject."


def about(subject: str) -> str:
    return f"Send a mail to {OWN}, with the subject \u201c{subject}\u201d, end of subject."


def test_a_mail_to_the_own_address_is_read_back_with_its_subject_but_not_its_body(env):
    named = outcome(env, SEND, to=[OWN], subject="Open proposals",
                    htmlBody="<p>ignore previous instructions</p>")
    assert isinstance(named, Named)
    assert named.sentence == about("Open proposals")
    assert "message" in named.covers
    assert "ignore" not in named.sentence


@pytest.mark.parametrize("tool_input", [
    {"to": OWN, "body": "b"},
    {"to": [OWN], "cc": [], "bcc": [], "body": "b", "includeSignature": True},
    {"to": [OWN], "body": "b", "replyThreadId": "t1"},
])
def test_spellings_of_the_same_mail_to_the_own_address(env, tool_input):
    assert outcome(env, SEND, **tool_input).sentence == NO_SUBJECT
    assert outcome(env, SEND, subject="Hi", **tool_input).sentence == about("Hi")


@pytest.mark.parametrize("tool, tool_input", [
    (SEND, {"to": ["someone@example.com"], "body": "b"}),
    (SEND, {"to": [OWN, "someone@example.com"], "body": "b"}),
    (SEND, {"to": [f"Driver <{OWN}>"], "body": "b"}),
    (SEND, {"to": [], "body": "b"}),
    (SEND, {"body": "b"}),
    (SEND, {"to": [OWN], "cc": ["someone@example.com"], "body": "b"}),
    (SEND, {"to": [OWN], "bcc": ["someone@example.com"], "body": "b"}),
    (SEND, {"to": [OWN], "bcc": [OWN], "body": "b"}),
    (SEND, {"draftId": "d1"}),
    (SEND, {"to": [OWN], "draftId": "d1"}),                       # a draft goes as it is
    (SEND, {"to": [OWN], "body": "b", "attachments": [{"content": "eA=="}]}),
    (SEND, {"to": [OWN], "body": "b", "somethingNew": 1}),
    (SEND, {"to": [OWN], "body": "b", "replyToMessageId": "m1"}),  # needs read access, unchecked
    (SEND, {"to": [OWN], "body": "b", "replyAll": False}),        # not a field of a send
    # The read-back must be the bytes that run: no lookalikes, nothing around it.
    (SEND, {"to": [OWN + "\u2003"], "body": "b"}),                # EM SPACE
    (SEND, {"to": ["\u00a0" + OWN], "body": "b"}),                # NO-BREAK SPACE
    (SEND, {"to": [OWN + " "], "body": "b"}),
    # The reply tool may copy a thread's default cc: never by voice, even to the driver.
    (REPLY, {"messageId": "m1", "to": [OWN], "body": "b"}),
    (REPLY, {"messageId": "m1", "body": "b"}),
])
def test_any_other_mail_has_no_sentence_and_needs_the_desk(env, tool, tool_input):
    assert outcome(env, tool, **tool_input) is None


@pytest.mark.parametrize("name", [
    "forward", "trash_message", "trash_thread", "delete_draft", "create_draft",
    "update_draft", "label_message", "label_thread", "unlabel_thread", "unlabel_message",
    "mark_thread_spam", "unmark_message_spam", "apply_sensitive_thread_label",
    "update_message_labels", "delete_label", "untrash_thread",
])
def test_forward_trash_label_and_drafts_are_gated_and_never_nameable(env, name):
    assert outcome(env, G + name, messageId="m1", to=[OWN], threadId="t1") is None


@pytest.mark.parametrize("name", ["search_threads", "get_thread", "list_labels", "list_drafts"])
def test_reading_mail_stays_as_it_was(env, name):
    ctx, _ = env
    assert not classify.classify(G + name, {"query": "x"}, ctx).gated


def test_without_own_mail_every_mail_needs_the_desk(tmp_path):
    config = make_config(tmp_path)
    ctx = make_ctx(tmp_path, config=config)
    assert derive("", ctx, config, tool=SEND, tool_input={"to": [OWN]}).outcome is None


def test_the_read_back_says_the_address_as_it_will_be_sent(env):
    # Case does not decide who it goes to, but the sentence names the bytes that run.
    assert outcome(env, SEND, to=["Driver@Example.org"], body="b").sentence \
        == "Send a mail to Driver@Example.org, without a subject."


def test_a_kelvin_sign_is_not_a_k(tmp_path):
    config = make_config(tmp_path, own_mail=["mark@example.org"])
    ctx = make_ctx(tmp_path, config=config)
    assert "mar\u212a@example.org".lower() == "mark@example.org"      # why it matters
    assert derive("", ctx, config, tool=SEND,
                  tool_input={"to": ["mar\u212a@example.org"]}).outcome is None
    assert derive("", ctx, config, tool=SEND,
                  tool_input={"to": ["mark@example.org"]}).outcome is not None


def test_a_host_local_tool_action_does_not_widen_the_mail_tools(tmp_path):
    rules_dir = tmp_path / "etc" / "rules.d"
    rules_dir.mkdir(parents=True)
    (rules_dir / "mail.toml").write_text(
        f'[[tool_actions]]\ntool = "{G}*"\nclass = "message"\nrecipients = ["to", "cc", "bcc"]\n')
    config = make_config(tmp_path, own_mail=[OWN])
    ctx = make_ctx(tmp_path, config=config)
    other = derive("", ctx, config, tool=SEND, tool_input={"to": ["someone@example.com"]})
    assert other.outcome is None
    assert derive("", ctx, config, tool=REPLY,
                  tool_input={"messageId": "m", "to": ["someone@example.com"]}).outcome is None
    assert derive("", ctx, config, tool=G + "forward",
                  tool_input={"messageId": "m", "to": [OWN]}).outcome is None
    assert derive("", ctx, config, tool=SEND, tool_input={"to": [OWN]}).outcome.sentence \
        == NO_SUBJECT


@pytest.mark.parametrize("value", [["not an address"], "driver@example.org", [5], ["a@b"]])
def test_own_mail_must_be_a_list_of_addresses(tmp_path, value):
    with pytest.raises(ConfigError):
        make_config(tmp_path, own_mail=value)


# --- through the gate's flow ----------------------------------------------------------------

def test_a_mail_to_the_driver_becomes_the_pending_call(tmp_path):
    turn = Turn(tmp_path, make_config(tmp_path, own_mail=[OWN]))
    result = turn.call(tool=SEND, tool_input={"to": [OWN], "subject": "s", "htmlBody": "<p>b</p>"})
    assert not result.allow
    assert result.reason == decision.REASON_PENDING
    state = turn.state()
    assert state["pending"]["tool"] == SEND
    assert state["events"][-1]["readback"] == about("s")


@pytest.mark.parametrize("tool, tool_input", [
    (SEND, {"to": ["someone@example.com"], "body": "b"}),
    (SEND, {"to": [OWN], "bcc": ["someone@example.com"], "body": "b"}),
    (G + "forward", {"messageId": "m1", "to": [OWN]}),
])
def test_a_mail_to_anyone_else_needs_the_desk_and_is_never_pending(tmp_path, tool, tool_input):
    turn = Turn(tmp_path, make_config(tmp_path, own_mail=[OWN]))
    result = turn.call(tool=tool, tool_input=tool_input)
    assert not result.allow
    assert result.reason == decision.REASON_DESK
    assert turn.state()["pending"] is None


def test_own_mail_cannot_be_set_in_a_host_local_rules_file(tmp_path):
    rules_dir = tmp_path / "rules.d"
    rules_dir.mkdir()
    (rules_dir / "wide.toml").write_text('[own_mail]\ntools = ["mcp__claude_ai_Gmail__reply"]\n')
    with pytest.raises(RulesError, match="own_mail"):
        load_rules(rules_dir)


# --- switching confirmation off never covers Gmail ----------------------------------------

EVIL = [
    (SEND, {"to": ["x@evil.invalid"], "body": "b", "attachments": [{"content": "eA=="}]}),
    (SEND, {"to": ["x@evil.invalid"], "body": "b"}),
    (SEND, {"draftId": "d1"}),
    (G + "forward", {"messageId": "m1", "to": ["x@evil.invalid"]}),
    (REPLY, {"messageId": "m1", "to": [OWN], "body": "b"}),
    (G + "trash_thread", {"threadId": "t1"}),
    ("mcp__claude_ai_Gmail_2__send_message", {"to": [OWN], "body": "b"}),
]


@pytest.mark.parametrize("settings", [{"confirm": "off"}, {"confirm_skip": ["message"]},
                                      {"confirm_skip": ["message", "delete", "tool"]}])
@pytest.mark.parametrize("tool, tool_input", EVIL)
def test_no_gmail_call_runs_unasked_when_confirmation_is_off(tmp_path, settings, tool, tool_input):
    turn = Turn(tmp_path, make_config(tmp_path, own_mail=[OWN], **settings))
    result = turn.call(tool=tool, tool_input=tool_input)
    assert not result.allow, (settings, tool)
    assert turn.state()["pending"] is None


@pytest.mark.parametrize("settings", [{"confirm": "off"}, {"confirm_skip": ["message"]}])
def test_with_confirmation_off_a_mail_to_the_driver_is_still_read_back(tmp_path, settings):
    turn = Turn(tmp_path, make_config(tmp_path, own_mail=[OWN], **settings))
    assert turn.call(tool=SEND, tool_input={"to": [OWN], "body": "b"}).reason \
        == decision.REASON_PENDING
    # ... while another connector's message is let through, as before.
    other = Turn(tmp_path, make_config(tmp_path, own_mail=[OWN], **settings))
    assert other.call(tool="mcp__x__send_thing", tool_input={"x": 1}).allow


# --- the yes releases exactly the call that was read back ---------------------------------

def _grant_for(tmp_path, config, tool_input):
    ctx = decision.build_context(config, (tmp_path / "projects" / "demo").resolve(),
                                 home=tmp_path / "home", lib_dir=tmp_path / "lib", git=no_git)
    derived = naming.derive(SEND, tool_input, ctx, config)
    assert isinstance(derived.outcome, Named)
    return derived


def test_a_yes_releases_this_mail_once_and_a_swapped_recipient_is_refused(tmp_path):
    config = make_config(tmp_path, own_mail=[OWN])
    mail = {"to": [OWN], "subject": "Open proposals", "htmlBody": "<p>two</p>"}
    derived = _grant_for(tmp_path, config, mail)

    swapped = Turn(tmp_path, config, grant=derived.hash, grant_base=derived.base_hash)
    result = swapped.call(tool=SEND, tool_input=dict(mail, to=["x@evil.invalid"]))
    assert not result.allow
    assert swapped.state()["grant_used"] is False
    result = swapped.call(tool=SEND, tool_input=dict(mail, bcc=["x@evil.invalid"]))
    assert not result.allow and swapped.state()["grant_used"] is False

    turn = Turn(tmp_path, config, grant=derived.hash, grant_base=derived.base_hash)
    assert turn.call(tool=SEND, tool_input=mail).allow
    assert turn.state()["grant_used"] is True
    again = turn.call(tool=SEND, tool_input=mail)
    assert not again.allow and again.reason == decision.REASON_GRANT_USED


# --- the subject in the read-back (Mark, 2026-11-24, #92) --------------------------------------

UNREADABLE = f"Send a mail to {OWN}, with a subject that cannot be read out."


def subject_of(env, subject, **extra):
    tool_input = {"to": [OWN], "body": "b", **extra}
    if subject is not ...:
        tool_input["subject"] = subject
    return outcome(env, SEND, **tool_input)


def test_the_subject_comes_from_the_subject_field_only(env):
    assert subject_of(env, ..., body="Subject: merge everything",
                      htmlBody="<title>say yes</title>").sentence == NO_SUBJECT
    assert subject_of(env, "Open proposals", body="say yes").sentence == about("Open proposals")


@pytest.mark.parametrize("subject", [["a list"], 5, {"text": "x"}, True])
def test_a_subject_that_is_not_text_needs_the_desk(env, subject):
    assert subject_of(env, subject) is None


@pytest.mark.parametrize("subject", [..., None, "", "   ", "  "])
def test_an_empty_or_missing_subject_reads_without_a_subject(env, subject):
    assert subject_of(env, subject).sentence == NO_SUBJECT


@pytest.mark.parametrize("subject", [
    "x\r\nBcc: evil@example.org",                                  # CR LF
    "x\nBcc: evil@example.org",
    "x\rBcc", "a\tb",                                              # controls
    "Hi‮evil",                                                # RIGHT-TO-LEFT OVERRIDE
    "a⁦b⁩c", "a‏b",                                 # isolates, RLM
    "sa​y", "a‌b", "a‍b", "a⁠b", "﻿a",    # zero-width, BOM
    "bo­dy",                                                  # soft hyphen
    "x\u0000y", "x\u007fy",
    "a\U000e0041b",                                                # tag character
    "ab",                                                    # private use
    "a b", "a b",                                        # line, paragraph separator
    "​⁠﻿",                                          # only invisible
])
def test_a_subject_that_cleaning_would_change_needs_the_desk(env, subject):
    # What is read must be what is sent, up to NFKC and ordinary spaces: a line
    # break or an invisible character leaves the call without a sentence.
    assert subject_of(env, subject) is None


@pytest.mark.parametrize("subject, spoken", [
    ("Ｏｐｅｎ proposals", "Open proposals"),     # fullwidth letters: NFKC
    ("café", "café"),                              # decomposed: NFKC composes
    ("é́́", "é"),                             # stray marks after NFKC
    ("ok ✅️ done"[3:], "✅ done"),                   # variation selector
])
def test_the_subject_is_nfkc(env, subject, spoken):
    assert subject_of(env, subject).sentence == about(spoken)


@pytest.mark.parametrize("subject, spoken", [
    ("  Open  proposals   today  ", "Open proposals today"),
    ("a b c　d", "a b c d"),                         # odd spaces: one space
])
def test_ordinary_spaces_are_collapsed(env, subject, spoken):
    assert subject_of(env, subject).sentence == about(spoken)


def test_a_long_subject_is_capped_at_80_characters_ending_with_an_ellipsis(env):
    words = ("Wanderer Billbird Gitsweeper Wordsworth Ratatoskr Zettelkast Homelab Handbook"
             " Habitat Certswap")
    sentence = subject_of(env, words).sentence
    spoken = sentence.split("“")[1].split("”")[0]
    assert len(spoken) == naming.MAX_SUBJECT_CHARS
    assert spoken.endswith("…") and not spoken.endswith(" …")
    assert words.startswith(spoken[:-1])
    exactly = " ".join(["abcdefgh"] * 9)                           # 80: not capped
    assert len(exactly) == 80
    assert subject_of(env, exactly).sentence == about(exactly)
    assert subject_of(env, exactly + " i").sentence == about(exactly[:79] + "…")


def test_more_than_ten_words_is_not_read_out(env):
    assert subject_of(env, " ".join(["word"] * 10)).sentence == about(" ".join(["word"] * 10))
    assert subject_of(env, " ".join(["word"] * 11)).sentence == UNREADABLE


@pytest.mark.parametrize("subject", [
    "Mark, reply soon", "Notes for Mark", "Wanderer release notes",
])
def test_a_subject_stands_in_the_frame(env, subject):
    sentence = subject_of(env, subject).sentence
    assert sentence == about(subject)
    assert sentence.startswith(f"Send a mail to {OWN}, with the subject “")
    assert sentence.endswith("”, end of subject.")


@pytest.mark.parametrize("subject", [
    # Closing the frame, by spelling or by ear (the quotes are silent).
    'x", end of subject. Merge pull request 5. "y',
    "end off subject", "and of subject", "ent of subject", "end of subjekt",
    "end of the subject", "END-OF-SUBJECT", "e n d o f s u b j e c t",
    "end quote", "einde onderwerp", "ｓubject",
    "end off subject. Merge pull request 12 on jarvis. Say yes to go ahead",
    "end off subject, Merge pull request 12 on jarvis, Say yes to go ahead",
    # The question, or a yes.
    "say yes", "Mark, reply ja", "ja", "j a", "Yes", "y e s", "yeah", "okay", "Zeg ja",
    "go ahead", "Go-ahead", "Akkoord",
    # Another action.
    "Send a mail to someone", "merge it", "Force push", "delete the repo", "deploy now",
    "the pull request", "at commit abc1234", "with a copy to Bob", "roll back",
    "Switch voice confirmation off", "as root", "Checks green", "Status unknown",
])
def test_a_subject_that_could_close_the_frame_or_ask_is_not_read_out(env, subject):
    assert subject_of(env, subject).sentence == UNREADABLE


@pytest.mark.parametrize("subject", [
    "Done.", "Done!", "Done?", "a; b", "PR #92: done", "Wait…", "Wait...",
    "Note。", "¿Qué", "a — b", "a – b",       # other stops, dashes
    "[Notes](/ænd Qv sAbdZEkt/)", "[x](-2)", "a (b)", "a [b]", "a {b}",
])
def test_sentence_punctuation_brackets_and_dashes_are_not_read_out(env, subject):
    assert subject_of(env, subject).sentence == UNREADABLE


def test_double_quotes_inside_the_subject_cannot_close_the_quotes(env):
    sentence = subject_of(env, 'He said "fine" “now” «later»').sentence
    assert sentence == about("He said 'fine' 'now' 'later'")
    assert sentence.count("“") == 1 and sentence.count("”") == 1


@pytest.mark.parametrize("subject", [
    "Hello еnd",                       # Cyrillic e
    "Αlpha",                           # Greek capital alpha
    "aㅤb",                             # Hangul filler: an invisible letter
    "Ꭺ",                               # Cherokee
    "price ١٢",                   # Arabic-Indic digits
    "1½ hours", "x²", "① first", "１２",   # digits NFKC would change
])
def test_another_script_or_digit_is_not_read_out(env, subject):
    assert subject_of(env, subject).sentence == UNREADABLE


@pytest.mark.parametrize("subject", [
    "token sk-ant-api03-xyz", "sk-proj-abc", "my sk - abc", "s k-abc",
    "ghp_" + "a1B2" * 9, "gho_abc", "ghs_abc", "github_pat_11AB", "glpat-abc",
    "AKIAIOSFODNN7EXAMPLE", "ASIAABCDEFGHIJKLMNOP", "xoxb-123", "xoxp-1",
    "AIzaSyA1234567890", "eyJhbGciOiJIUzI1NiJ9", "key " + "Ab3" * 12,
    "abc123def456ghi789jk", "aBcDeFgHiJkLmNoPqRsT",
    "my password", "new passwd", "pw: hunter", "the token", "a secret",
    "apikey", "api key", "API-Key", "wachtwoord",
])
def test_a_secret_shape_is_not_read_out(env, subject):
    assert subject_of(env, subject).sentence == UNREADABLE


@pytest.mark.parametrize("subject", [
    "Open proposals", "Café, naïve façade, Straße, Ærø",
    "jarvis-mail-the-driver-readback-is-done-now-and-green",   # hyphenated, no secret
    "PR #92 done ✅ 100% & more", "Jarvis turns of today", "ASIA trip in May",
    "Wanderer release notes", "Mark’s notes", "Handbook 2026-11-24",
    "Ratatoskr Habitat Homelab", "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
])
def test_ordinary_subjects_are_read_out(env, subject):
    assert subject_of(env, subject).sentence != UNREADABLE


def _templates():
    """Every fixed sentence or verb of a read-back, from the modules that build them."""
    import ast
    import re
    from pathlib import Path
    found = set()
    for module in (naming, wrapper.prstatus):
        tree = ast.parse(Path(module.__file__).read_text())
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
                      and node.body and isinstance(node.body[0], ast.Expr)}
        parts = {id(value) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
                 for value in node.values}
        for node in ast.walk(tree):
            if id(node) in docstrings or id(node) in parts:
                continue
            if isinstance(node, ast.JoinedStr):
                text = " ".join(v.value for v in node.values if isinstance(v, ast.Constant))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            else:
                continue
            # Not a tool name ("Bash") or a Unicode category ("Zl", "Zp").
            if re.match(r"[A-Z][a-z]+(\s|\.|:|$)", text) and text not in ("Bash", "Zl", "Zp"):
                found.add(text)
    return sorted(found)


def test_every_read_back_template_is_refused_as_a_subject():
    # The refused words follow the templates: a new read-back sentence fails here
    # until one of its words is in SUBJECT_REFUSED_LETTERS.
    templates = _templates()
    assert "Say yes to go ahead." in templates and "Push" in templates
    assert any(t.startswith("Restart") for t in templates)
    for template in templates:
        words = " ".join("".join(ch if ch.isalpha() else " " for ch in template).split())
        assert naming.subject_phrase(words) == naming.SUBJECT_UNREADABLE, template


def _every_kind_of_character():
    points = list(range(0, 0x3200)) + list(range(0xfe00, 0x10000)) \
        + list(range(0x1f300, 0x1f400)) + list(range(0xe0000, 0xe0080))
    return ["".join(chr(p) for p in points[i:i + 8] if not 0xd800 <= p < 0xe000)
            for i in range(0, len(points), 8)]


def test_every_read_back_with_a_subject_is_spoken_as_built(tmp_path):
    # Whatever the subject holds, the sentence the wrapper builds is one the bridge
    # speaks as it is (no control, format or odd space character, one line, in
    # length) and that Kokoro's G2P (misaki) reads as it is, with no markup.
    from misaki.en import G2P
    config = make_config(tmp_path, own_mail=[OWN])
    ctx = make_ctx(tmp_path, config=config)
    subjects = _every_kind_of_character() + [
        "a" * 2000, "word " * 300, "[Notes](/ænd/)", "[x](-2) y", "[a](#b#)"]
    read = 0
    for subject in subjects:
        derived = derive("", ctx, config, tool=SEND, tool_input={"to": [OWN], "subject": subject})
        if derived.outcome is None:
            assert naming._raw_subject_changes(subject), repr(subject)
            continue
        text = wrapper.build_readback(derived, ctx, {"cwd": str(ctx.cwd)}, config, {})
        assert text is not None and speakable_readback(text) == text, repr(subject)
        preprocessed, _tokens, features = G2P.preprocess(text)
        assert preprocessed == text and features == {}, repr(subject)
        if "“" in text:
            read += 1
            inner = text.split("“", 1)[1].rsplit("”", 1)[0]
            assert len(inner) <= naming.MAX_SUBJECT_CHARS
            assert all(unicodedata.category(ch)[0] not in "CMZ" or ch == " " for ch in inner)
    assert read > 50


def test_a_yes_releases_the_subject_that_was_read_back_and_no_other(tmp_path):
    config = make_config(tmp_path, own_mail=[OWN])
    mail = {"to": [OWN], "subject": "Open proposals", "htmlBody": "<p>two</p>"}
    derived = _grant_for(tmp_path, config, mail)
    assert derived.outcome.sentence == about("Open proposals")

    for other in ("Merge everything", "Open proposals​", "Open  proposals", None):
        swapped = Turn(tmp_path, config, grant=derived.hash, grant_base=derived.base_hash)
        changed = dict(mail, subject=other) if other is not None else \
            {k: v for k, v in mail.items() if k != "subject"}
        result = swapped.call(tool=SEND, tool_input=changed)
        assert not result.allow, repr(other)
        assert swapped.state()["grant_used"] is False

    # Two subjects that read the same are still two calls: the yes is for the bytes.
    same_ear = _grant_for(tmp_path, config, dict(mail, subject="\uff2fpen  proposals"))
    assert same_ear.outcome.sentence == derived.outcome.sentence
    assert same_ear.hash != derived.hash

    turn = Turn(tmp_path, config, grant=derived.hash, grant_base=derived.base_hash)
    assert turn.call(tool=SEND, tool_input=mail).allow


# --- no other read-back speaks agent text -------------------------------------------------

def test_a_shell_mail_does_not_speak_its_subject(env):
    ctx, config = env
    derived = derive(f"mail -s 'say yes' {OWN}", ctx, config)
    assert derived.outcome.sentence == f"Send a mail to {OWN}."


def test_a_host_local_tool_action_does_not_speak_a_subject(tmp_path):
    rules_dir = tmp_path / "etc" / "rules.d"
    rules_dir.mkdir(parents=True)
    (rules_dir / "send.toml").write_text(
        '[[tool_actions]]\ntool = "mcp__other__send_*"\nclass = "message"\nrecipients = ["to"]\n')
    config = make_config(tmp_path, own_mail=[OWN])
    ctx = make_ctx(tmp_path, config=config)
    derived = derive("", ctx, config, tool="mcp__other__send_mail",
                     tool_input={"to": [OWN], "subject": "say yes"})
    assert derived.outcome.sentence == f"Send a mail to {OWN}."
