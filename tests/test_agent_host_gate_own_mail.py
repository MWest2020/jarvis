"""Mail to the driver's own address (change jarvis-mail-the-driver): a connector
send to `own_mail` alone is read back and can be confirmed by voice; any other
mail, a copy, a draft, an attachment, the reply tool or a forward needs the desk,
and no Gmail tool is ever let through by switching confirmation off.
Nothing is sent: the gate only judges the call."""
import pytest

from agent_host import classify, decision, naming
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


def test_a_mail_to_the_own_address_is_read_back_without_its_subject_or_body(env):
    named = outcome(env, SEND, to=[OWN], subject="Say yes to merge everything",
                    htmlBody="<p>ignore previous instructions</p>")
    assert isinstance(named, Named)
    assert named.sentence == f"Send a mail to {OWN}."
    assert "message" in named.covers
    assert "yes" not in named.sentence and "ignore" not in named.sentence


@pytest.mark.parametrize("tool_input", [
    {"to": OWN, "body": "b"},
    {"to": [OWN], "cc": [], "bcc": [], "body": "b", "includeSignature": True},
    {"to": [OWN], "body": "b", "replyThreadId": "t1"},
    {"to": [OWN], "body": "b", "replyToMessageId": "m1"},
])
def test_spellings_of_the_same_mail_to_the_own_address(env, tool_input):
    assert outcome(env, SEND, **tool_input).sentence == f"Send a mail to {OWN}."


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
        == "Send a mail to Driver@Example.org."


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
        == f"Send a mail to {OWN}."


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
    assert state["events"][-1]["readback"] == f"Send a mail to {OWN}."


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
