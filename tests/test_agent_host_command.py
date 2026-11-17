from pathlib import Path

from agent_host.command import FORBIDDEN_OPTIONS, build_command, settings_default_to_bypass

SETTINGS = Path("/usr/local/lib/jarvis-agent/turn-settings.json")
PROMPT = Path("/usr/local/lib/jarvis-agent/prompt.md")


def test_the_argv_matches_d34_byte_for_byte_with_no_prior_session():
    command = build_command(settings_path=SETTINGS, prompt_path=PROMPT, session_id=None)
    assert command == [
        "claude", "-p", "--output-format", "json",
        "--permission-mode", "auto", "--permission-prompts", "none",
        "--system-prompt-snapshot", "off",
        "--append-system-prompt-file", str(PROMPT),
        "--settings", str(SETTINGS),
    ]


def test_the_argv_appends_resume_as_one_token():
    command = build_command(
        settings_path=SETTINGS, prompt_path=PROMPT,
        session_id="11111111-1111-1111-1111-111111111111",
    )
    assert command[-1] == "--resume=11111111-1111-1111-1111-111111111111"
    # Never two argv elements: --resume takes an optional value, so a stored
    # id that slipped through validation as an option-looking string must
    # not become its own argument.
    assert "--resume" not in command


def test_the_inline_prompt_fallback_form():
    command = build_command(
        settings_path=SETTINGS, prompt_path=PROMPT, session_id=None,
        append_prompt_inline=True, prompt_text="be jarvis",
    )
    assert "--append-system-prompt" in command
    assert command[command.index("--append-system-prompt") + 1] == "be jarvis"
    assert "--append-system-prompt-file" not in command


def test_the_session_rotation_fallback_is_just_the_absence_of_resume():
    # D34's other fallback (a new session after 04:00) is the caller's
    # decision not to pass a session_id; the argv shape is identical to a
    # fresh project.
    command = build_command(settings_path=SETTINGS, prompt_path=PROMPT, session_id=None)
    assert "--resume" not in " ".join(command)


def test_no_forbidden_option_is_ever_on_the_line():
    for session_id in (None, "11111111-1111-1111-1111-111111111111"):
        for inline in (False, True):
            command = build_command(
                settings_path=SETTINGS, prompt_path=PROMPT, session_id=session_id,
                append_prompt_inline=inline, prompt_text="x",
            )
            for forbidden in FORBIDDEN_OPTIONS:
                assert forbidden not in command
            assert "bypassPermissions" not in command
            assert command[command.index("--permission-mode") + 1] == "auto"


def test_settings_default_to_bypass_top_level(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"defaultMode": "bypassPermissions"}')
    assert settings_default_to_bypass(path) is True


def test_settings_default_to_bypass_nested(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"permissions": {"defaultMode": "bypassPermissions"}}')
    assert settings_default_to_bypass(path) is True


def test_settings_default_to_bypass_false_for_auto(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"permissions": {"defaultMode": "auto"}}')
    assert settings_default_to_bypass(path) is False


def test_settings_default_to_bypass_false_when_missing(tmp_path):
    assert settings_default_to_bypass(tmp_path / "does-not-exist.json") is False


# --- the voice profile (voice-and-speed) ---------------------------------------------

def test_the_voice_profile_adds_model_and_effort_as_single_tokens():
    command = build_command(settings_path=SETTINGS, prompt_path=PROMPT, session_id=None,
                            model="sonnet", effort="low")
    assert command[:12] == [
        "claude", "-p", "--output-format", "json",
        "--permission-mode", "auto", "--permission-prompts", "none",
        "--system-prompt-snapshot", "off", "--model=sonnet", "--effort=low",
    ]
    assert "--model" not in command and "--effort" not in command


def test_a_full_model_name_is_accepted():
    for name in ("claude-sonnet-5-5", "opus[1m]", "claude-haiku-4-5-20251001"):
        command = build_command(settings_path=SETTINGS, prompt_path=PROMPT, session_id=None,
                                model=name)
        assert f"--model={name}" in command


def test_a_model_or_effort_that_could_be_an_option_is_refused():
    import pytest
    for model in ("--dangerously-skip-permissions", "-x", "", "a b", "sonnet\n"):
        with pytest.raises(ValueError):
            build_command(settings_path=SETTINGS, prompt_path=PROMPT, session_id=None,
                          model=model)
    for effort in ("none", "LOW", "--tools", ""):
        with pytest.raises(ValueError):
            build_command(settings_path=SETTINGS, prompt_path=PROMPT, session_id=None,
                          effort=effort)
