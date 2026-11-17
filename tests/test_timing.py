"""Per-stage timing (voice-and-speed): the bridge's timer and header, the log
line of a turn, and the agent host's timing in a turn record."""
import io
import json
import logging
import subprocess
import wave

from agent_host import timing as host_timing
from agent_host.wrapper import handle_request
from bridge.timing import TurnTimer, wav_seconds
from tests.test_agent_host_wrapper import _config, _popen, _raw
from tests.test_server_full_mode import Bridge


def _wav(seconds: float, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\0\0" * int(seconds * rate))
    return buffer.getvalue()


def timing_lines(caplog, count: int, *, within: float = 5.0) -> list[str]:
    """The `timing` log lines, once `count` of them are there. The handler logs the line
    after the reply's last byte is written, on its own (daemon) thread, so a client can
    have read the whole reply before the line exists: wait for it, with a deadline."""
    import time
    deadline = time.monotonic() + within
    while True:
        lines = [r.getMessage() for r in caplog.records if " timing " in r.getMessage()]
        if len(lines) >= count or time.monotonic() >= deadline:
            return lines
        time.sleep(0.01)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


# --- the bridge -------------------------------------------------------------------

def test_wav_seconds_reads_the_header():
    assert wav_seconds(_wav(2.5)) == 2.5
    assert wav_seconds(_wav(1.0, rate=24000)) == 1.0


def test_wav_seconds_is_none_for_what_is_not_a_wav():
    assert wav_seconds(b"audio") is None
    assert wav_seconds(b"") is None


def test_the_timer_adds_stages_in_milliseconds_and_keeps_their_order():
    clock = Clock()
    timer = TurnTimer(clock)
    with timer.stage("agent"):
        clock.now += 7.6
    with timer.stage("stt"):
        clock.now += 0.9
    with timer.stage("tts"):
        clock.now += 5.4
    timer.audio_in_s, timer.audio_out_s, timer.spoken_chars = 5.5, 9.96, 143
    assert timer.header_value() == "stt=900;agent=7600;tts=5400;total=13900"
    assert timer.log_fields() == ("stt_ms=900 agent_ms=7600 tts_ms=5400 total_ms=13900 "
                                  "audio_in_s=5.5 audio_out_s=10.0 spoken_chars=143")


def test_a_wrapped_function_is_timed_on_every_call():
    clock = Clock()
    timer = TurnTimer(clock)

    def measure():
        clock.now += 0.05
        return 0.7
    timed = timer.wrap("vad", measure)
    assert timed() == 0.7 and timed() == 0.7
    assert timer.ms == {"vad": 100}


def test_a_full_turn_sends_the_timing_header_and_logs_one_line(tmp_path, monkeypatch, caplog):
    # For the whole test, not a with-block: the line comes after the reply (timing_lines).
    caplog.set_level(logging.INFO, logger="bridge")
    b = Bridge(tmp_path, monkeypatch)
    try:
        response, said = b.say("what is open?")
        lines = timing_lines(caplog, 1)
    finally:
        b.stop()
    assert said == "WAV:Done on the agent host."
    header = response.getheader("X-Jarvis-Timing")
    names = [part.split("=")[0] for part in header.split(";")]
    assert names == ["read", "stt", "vad", "agent", "tts", "total"]
    assert all(part.split("=")[1].isdigit() for part in header.split(";"))
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("project=demo timing ")
    for field in ("stt_ms=", "vad_ms=", "agent_ms=", "tts_ms=", "write_ms=", "total_ms=",
                  "spoken_chars=23"):
        assert field in line
    # The stub's bytes are no WAV: no seconds are made up for them.
    assert "audio_in_s" not in line and "what is open" not in line


def test_a_silent_recording_is_timed_too(tmp_path, monkeypatch):
    b = Bridge(tmp_path, monkeypatch)
    try:
        response, _ = b.say("   ")
    finally:
        b.stop()
    names = [part.split("=")[0] for part in response.getheader("X-Jarvis-Timing").split(";")]
    assert names == ["read", "stt", "tts", "total"]


# --- the agent host ---------------------------------------------------------------

CLAUDE_JSON = {
    "result": "done.\nSPOKEN SUMMARY: done.", "session_id": "22222222-2222-2222-2222-222222222222",
    "duration_ms": 2165, "duration_api_ms": 2002, "num_turns": 1, "ttft_ms": 2141,
    "usage": {"input_tokens": 2, "output_tokens": 40, "cache_read_input_tokens": 11266,
              "cache_creation_input_tokens": 11023,
              "output_tokens_details": {"thinking_tokens": 12}},
    "modelUsage": {"claude-opus-5-5": {}},
}


def test_from_claude_takes_the_numbers_it_knows():
    assert host_timing.from_claude(CLAUDE_JSON) == {
        "duration_ms": 2165, "duration_api_ms": 2002, "num_turns": 1, "ttft_ms": 2141,
        "input_tokens": 2, "output_tokens": 40, "cache_read_tokens": 11266,
        "cache_creation_tokens": 11023, "thinking_tokens": 12, "models": ["claude-opus-5-5"],
    }


def test_from_claude_ignores_what_is_not_a_count():
    assert host_timing.from_claude({"duration_ms": "2165", "num_turns": True,
                                    "duration_api_ms": -1, "usage": []}) == {}
    assert host_timing.from_claude(None) == {}


def test_the_startup_gap_is_the_wall_time_outside_claudes_own_clock():
    timing = host_timing.turn_timing(pre_s=0.04, wall_s=3.1, payload=CLAUDE_JSON)
    assert timing["wrapper_pre_ms"] == 40 and timing["claude_wall_ms"] == 3100
    assert timing["startup_gap_ms"] == 3100 - 2165


def test_a_turn_record_carries_the_timing(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=json.dumps(CLAUDE_JSON)))
    config = _config(tmp_path)
    reply = handle_request(_raw(), config)
    record = json.loads((config.records_dir / f"{reply['turn']}.json").read_text())
    timing = record["timing"]
    for key in ("wrapper_pre_ms", "claude_wall_ms", "wrapper_post_ms", "duration_api_ms",
                "num_turns", "startup_gap_ms", "output_tokens"):
        assert key in timing
    assert timing["duration_api_ms"] == 2002 and timing["num_turns"] == 1


def test_a_failed_turn_still_has_the_wrappers_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(returncode=1, stderr="boom"))
    config = _config(tmp_path)
    reply = handle_request(_raw(), config)
    timing = json.loads((config.records_dir / f"{reply['turn']}.json").read_text())["timing"]
    assert set(timing) == {"wrapper_pre_ms", "claude_wall_ms", "wrapper_post_ms"}


def test_the_record_names_the_voice_profile_and_the_argv_carries_it(tmp_path, monkeypatch):
    from tests.test_agent_host_wrapper import FakePopen
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=json.dumps(CLAUDE_JSON)))
    config = _config(tmp_path, voice_model="sonnet", voice_effort="low")
    reply = handle_request(_raw(), config)
    assert "--model=sonnet" in FakePopen.last.command
    assert "--effort=low" in FakePopen.last.command
    timing = json.loads((config.records_dir / f"{reply['turn']}.json").read_text())["timing"]
    assert timing["profile"] == {"model": "sonnet", "effort": "low"}


def test_without_a_voice_profile_the_argv_is_as_before(tmp_path, monkeypatch):
    from tests.test_agent_host_wrapper import FakePopen
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=json.dumps(CLAUDE_JSON)))
    config = _config(tmp_path)
    reply = handle_request(_raw(), config)
    assert not any(a.startswith(("--model", "--effort")) for a in FakePopen.last.command)
    timing = json.loads((config.records_dir / f"{reply['turn']}.json").read_text())["timing"]
    assert "profile" not in timing


# --- Whisper's decoding reaches faster-whisper ---------------------------------------

def test_transcribe_passes_beam_and_language_to_faster_whisper(monkeypatch):
    from bridge import stt
    seen = {}

    class Segment:
        text = " hello "

    class Model:
        def transcribe(self, audio, **kwargs):
            seen.update(kwargs)
            return [Segment()], None
    monkeypatch.setattr(stt, "_model", lambda size: Model())
    assert stt.transcribe(b"x", model_size="base") == "hello"
    assert seen == {"beam_size": 5, "language": None}
    stt.transcribe(b"x", model_size="base", beam_size=1, language="en")
    assert seen == {"beam_size": 1, "language": "en"}


def test_the_server_hands_the_whisper_options_to_transcribe(tmp_path, monkeypatch):
    import dataclasses

    from bridge import server, stt
    b = Bridge(tmp_path, monkeypatch)
    b.stop()
    seen = {}

    def transcribe(audio, model_size, **options):
        seen.update(options)
        return "what is open?"
    monkeypatch.setattr(stt, "transcribe", transcribe)
    b.config = dataclasses.replace(b.config, whisper_beam_size=1, whisper_language="en")
    b.start()
    try:
        b.say("what is open?")
    finally:
        b.stop()
    assert seen == {"beam_size": 1, "language": "en"}
    assert server.Handler.config.whisper_options == {"beam_size": 1, "language": "en"}


def test_wav_seconds_never_raises_on_a_broken_header():
    from bridge.timing import wav_seconds
    good = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
    for data in (b"", b"RIFF", good, good + b"\xff" * 7, b"RIFF\xff\xff\xff\xffWAVEdata"):
        assert wav_seconds(data) is None or isinstance(wav_seconds(data), float)
