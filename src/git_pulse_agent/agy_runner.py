"""Antigravity CLI (`agy`) dynamic model discovery, fallback selection, and audit execution."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

QUOTA_ERROR_MARKERS = (
    "quota",
    "rate limit",
    "rate_limit",
    "resource_exhausted",
    "429",
    "capacity",
    "overloaded",
    "unavailable",
)


@dataclass
class AgyResult:
    ok: bool
    model: str
    output: str
    error: str = ""
    quota_exceeded: bool = False


def parse_agy_models_output(raw_output: str) -> list[str]:
    """Parse tab-separated `agy models` output into ordered model IDs."""
    models: list[str] = []
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("fetching"):
            continue
        parts = stripped.split("\t", 1)
        model_id = parts[0].strip()
        if model_id and " " not in model_id:
            models.append(model_id)
    return models


def list_available_models(agy_bin: str = "agy") -> list[str]:
    """Query `agy models` for currently available model IDs."""
    if not shutil.which(agy_bin):
        return []
    res = subprocess.run(
        [agy_bin, "models"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if res.returncode != 0:
        return []
    return parse_agy_models_output(res.stdout)


def select_model_order(
    available: list[str],
    preferred: list[str] | None = None,
) -> list[str]:
    """Order candidate models: preferred available ones first, then remaining available models."""
    ordered: list[str] = []
    avail_set = set(available)
    for m in preferred or []:
        if m in avail_set and m not in ordered:
            ordered.append(m)
    for m in available:
        if m not in ordered:
            ordered.append(m)
    return ordered


def _looks_like_quota_error(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in QUOTA_ERROR_MARKERS)


def run_agy_prompt(
    prompt: str,
    cwd: str | Path,
    *,
    agy_bin: str = "agy",
    preferred_models: list[str] | None = None,
    timeout_seconds: int = 600,
    max_model_attempts: int = 3,
) -> AgyResult:
    """Execute a non-interactive `agy -p` audit, falling back across available models on quota errors."""
    if not shutil.which(agy_bin):
        return AgyResult(ok=False, model="", output="", error=f"`{agy_bin}` binary not found on PATH.")

    available = list_available_models(agy_bin)
    candidates = select_model_order(available, preferred_models)
    if not candidates:
        candidates = [""]  # let agy use its default configured model

    last_err = ""
    for model in candidates[:max_model_attempts]:
        cmd = [
            agy_bin,
            "-p", prompt,
            "--mode", "auto",
            "--dangerously-skip-permissions",
            "--disable-slash-commands",
            "--output-format", "text",
            "--print-timeout", f"{timeout_seconds}s",
        ]
        if model:
            cmd.extend(["--model", model])

        try:
            res = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout_seconds + 30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            last_err = f"Timed out after {timeout_seconds}s on model {model or 'default'}."
            continue

        combined = (res.stdout or "") + "\n" + (res.stderr or "")
        if res.returncode == 0 and not _looks_like_quota_error(res.stderr or ""):
            return AgyResult(ok=True, model=model or "default", output=res.stdout.strip())

        if _looks_like_quota_error(combined):
            last_err = f"Quota/capacity error on {model or 'default'}"
            continue

        return AgyResult(
            ok=False,
            model=model or "default",
            output=res.stdout.strip(),
            error=(res.stderr or res.stdout).strip()[:500],
        )

    return AgyResult(
        ok=False,
        model="",
        output="",
        error=last_err or "All candidate models failed.",
        quota_exceeded=True,
    )
