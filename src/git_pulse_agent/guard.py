"""Safety policies: protected (PR-only) repository guard and secret/privacy scanner."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Iterable

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("notion_token", re.compile(r"\bntn_[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("private_key_header", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
]

SENSITIVE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "token",
    "credentials.json",
    "secrets.json",
    ".sudo_pass",
    "voice_api_token",
}

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".cache"}


def is_protected_repo(
    repo_name: str,
    repo_path: str,
    remote_url: str,
    patterns: Iterable[str],
) -> bool:
    """Return True if the repository must NEVER receive direct commits (PR-only mode)."""
    haystacks = [repo_name.lower(), repo_path.lower(), (remote_url or "").lower()]
    for raw_pat in patterns:
        pat = raw_pat.strip().lower()
        if not pat:
            continue
        for h in haystacks:
            if pat in h or fnmatch.fnmatch(h, pat):
                return True
    return False


def scan_directory_for_secrets(
    root_dir: str | Path,
    max_file_bytes: int = 512_000,
) -> list[dict[str, str]]:
    """Scan a local directory for hardcoded secrets or sensitive files before initializing a repo."""
    findings: list[dict[str, str]] = []
    root = Path(root_dir)
    if not root.is_dir():
        return findings

    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue

        rel = str(path.relative_to(root))
        if path.name.lower() in SENSITIVE_FILENAMES or path.suffix.lower() in (".pem", ".key", ".p12"):
            findings.append({"file": rel, "rule": "sensitive_filename"})
            continue

        try:
            if path.stat().st_size > max_file_bytes:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for rule_name, regex in SECRET_PATTERNS:
            if regex.search(text):
                findings.append({"file": rel, "rule": rule_name})
                break

    return findings


def generate_safe_gitignore() -> str:
    """Return a defensive default .gitignore for newly promoted private repos."""
    return (
        "# Secrets & credentials\n"
        ".env\n"
        ".env.*\n"
        "*.key\n"
        "*.pem\n"
        "*.token\n"
        "*secret*\n"
        "*password*\n"
        "credentials.json\n\n"
        "# Runtime & build artifacts\n"
        "__pycache__/\n"
        "*.py[cod]\n"
        ".venv/\n"
        "venv/\n"
        "node_modules/\n"
        "dist/\n"
        "build/\n"
        "*.log\n"
    )
