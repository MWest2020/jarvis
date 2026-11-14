"""Fetch the speech models into HF_HOME at pinned commits (image build only).

The bridge loads its models by name (faster-whisper "base", Kokoro's default
repo), which the Hugging Face cache resolves through refs/main. This script
downloads exactly the pinned commit of each repo and points refs/main at it,
so the offline warm-up that follows in the Dockerfile can only use the pinned
files; a file this list misses makes that warm-up fail the build.

Usage: python fetch_models.py <whisper-size> <voice> [<voice> ...]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

# Bump deliberately; the SHA is the commit on huggingface.co.
WHISPER_REPOS = {
    "base": ("Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"),
}
KOKORO_REPO = ("hexgrad/Kokoro-82M", "f3ff3571791e39611d31c381e3a41a3af07b4987")


def fetch(repo_id: str, revision: str, allow_patterns: list[str]) -> None:
    snapshot_download(repo_id, revision=revision, allow_patterns=allow_patterns)
    hub = Path(os.environ["HF_HOME"]) / "hub"
    refs = hub / f"models--{repo_id.replace('/', '--')}" / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(revision)
    print(f"fetched {repo_id}@{revision} ({', '.join(allow_patterns)})")


def main() -> None:
    whisper_size, voices = sys.argv[1], sys.argv[2:]
    if whisper_size not in WHISPER_REPOS:
        raise SystemExit(f"no pinned revision for whisper size {whisper_size!r}; add it here")
    if not voices:
        raise SystemExit("name at least one Kokoro voice")
    repo, rev = WHISPER_REPOS[whisper_size]
    fetch(repo, rev, ["config.json", "preprocessor_config.json", "model.bin",
                      "tokenizer.json", "vocabulary.*"])
    repo, rev = KOKORO_REPO
    fetch(repo, rev, ["config.json", "kokoro-v1_0.pth"] + [f"voices/{v}.pt" for v in voices])


if __name__ == "__main__":
    main()
