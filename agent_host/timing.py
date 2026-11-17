"""Where a turn's time went, for its record (voice-and-speed, per-stage timing).

Three numbers come from the wrapper's own clock:

- `wrapper_pre_ms`: from the request arriving on stdin to `claude` being
  started (the checks, the lock, the session store, the environment);
- `claude_wall_ms`: from `claude` being started to it exiting;
- `wrapper_post_ms`: from `claude` exiting to the record being written (the
  gate's state, a read-back, a notify command, a clone).

The rest is what `claude -p --output-format json` says about itself, copied
only when it has the expected type: `duration_ms` (its own clock),
`duration_api_ms` (time spent waiting on the API), `num_turns` (model calls),
`ttft_ms` (to the first token), and token counts. `startup_gap_ms` is
`claude_wall_ms - duration_ms`: what Claude Code spends before and after its
own clock runs (starting node, reading settings, connecting MCP servers,
loading the session).

Nothing here decides anything; the record is the only reader.
"""
from __future__ import annotations

# Copied from the top level of claude's JSON reply when they are whole numbers.
_TOP_LEVEL = ("duration_ms", "duration_api_ms", "num_turns", "ttft_ms")
# Copied from its `usage` object, under these names.
_USAGE = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_input_tokens": "cache_read_tokens",
    "cache_creation_input_tokens": "cache_creation_tokens",
}


def _whole(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value < 0:   # NaN, or a negative count
        return None
    return int(round(value))


def ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def from_claude(payload: object) -> dict:
    """The timing and token fields of claude's JSON reply, as far as present."""
    if not isinstance(payload, dict):
        return {}
    out: dict = {}
    for key in _TOP_LEVEL:
        value = _whole(payload.get(key))
        if value is not None:
            out[key] = value
    usage = payload.get("usage")
    if isinstance(usage, dict):
        for key, name in _USAGE.items():
            value = _whole(usage.get(key))
            if value is not None:
                out[name] = value
        details = usage.get("output_tokens_details")
        if isinstance(details, dict):
            value = _whole(details.get("thinking_tokens"))
            if value is not None:
                out["thinking_tokens"] = value
    models = payload.get("modelUsage")
    if isinstance(models, dict):
        out["models"] = sorted(str(name)[:64] for name in models)[:8]
    return out


def turn_timing(*, pre_s: float, wall_s: float, payload: object,
                profile: dict | None = None) -> dict:
    """The `timing` object of a turn record, without `wrapper_post_ms` (added
    when the record is written)."""
    timing: dict = {"wrapper_pre_ms": ms(pre_s), "claude_wall_ms": ms(wall_s)}
    timing.update(from_claude(payload))
    if "duration_ms" in timing:
        timing["startup_gap_ms"] = max(0, timing["claude_wall_ms"] - timing["duration_ms"])
    if profile:
        timing["profile"] = dict(profile)
    return timing
