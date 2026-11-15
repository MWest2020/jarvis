# The jarvis bridge as a container image (run-bridge-in-cluster, D3).
# Everything the bridge needs at start is baked in: the Python environment
# (uv sync --frozen, CPU-only torch), the Claude Code CLI, and the
# faster-whisper and Kokoro weights, so a pod needs no internet for models.
# Build context = repo root:  docker build -f deploy/Dockerfile .
FROM node:22.23.5-bookworm-slim

# uv (pinned), copied from the official distroless image.
COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /uvx /bin/

# git: the project sync (deploy/sync-projects.sh; its repository list, with
# JARVIS_SYNC_REPOS=*, is deploy/list_repos.py on the venv's Python). ca-certificates: HTTPS to
# GitHub and the Anthropic API. libsndfile comes with the soundfile wheel and
# espeak-ng with the espeakng-loader wheel, so no other system packages.
# openssh-client: the one ssh connection per turn to the agent host in `full`
# mode (jarvis-full-agent, D32). Unused, and never started, in `readonly` mode.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates openssh-client \
 && rm -rf /var/lib/apt/lists/*

# Claude Code headless, pinned: the version the bridge's argv (--tools,
# dontAsk, --strict-mcp-config) was tested with. Never `latest`.
ARG CLAUDE_CODE_VERSION=2.1.322
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
 && npm cache clean --force

ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    HF_HOME=/opt/jarvis/hf

WORKDIR /opt/jarvis/app

# Dependencies first (cached layer), then the bridge itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv python install 3.12 \
 && uv sync --frozen --no-dev --no-install-project --python 3.12
COPY bridge/ ./bridge/
RUN uv sync --frozen --no-dev --no-editable --python 3.12

# Model weights at build time, at pinned Hugging Face commits (fetch_models.py
# points refs/main at them). Then the bridge's own warm-up runs OFFLINE, exactly
# as at start: it proves the pinned files are all it needs. Extra Kokoro voices:
# --build-arg KOKORO_VOICES="af_heart bf_emma" (Whisper sizes need a pin added).
ARG WHISPER_MODEL=base
ARG KOKORO_VOICES=af_heart
COPY deploy/fetch_models.py /opt/jarvis/fetch_models.py
RUN .venv/bin/python /opt/jarvis/fetch_models.py "$WHISPER_MODEL" $KOKORO_VOICES \
 && HF_HUB_OFFLINE=1 .venv/bin/python -c "import sys; from bridge import stt, tts; \
stt.warm_up(sys.argv[1]); [tts.synthesize('Ready.', voice=v) for v in sys.argv[2:]]" \
    "$WHISPER_MODEL" $KOKORO_VOICES \
 && chmod -R a+rX /opt/jarvis /opt/python

COPY deploy/sync-projects.sh /usr/local/bin/jarvis-sync-projects
COPY deploy/list_repos.py /usr/local/bin/jarvis-list-repos
RUN chmod 755 /usr/local/bin/jarvis-sync-projects /usr/local/bin/jarvis-list-repos \
 && useradd -m -u 10001 jarvis

# At run time: models from the image only, no hub calls, no Claude Code
# auto-update or telemetry.
ENV PATH=/opt/jarvis/app/.venv/bin:$PATH \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    PYTHONUNBUFFERED=1 \
    JARVIS_WHISPER_MODEL=${WHISPER_MODEL}

USER 10001
EXPOSE 8787
CMD ["jarvis-bridge"]
