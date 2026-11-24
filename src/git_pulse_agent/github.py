"""GitHub CLI (`gh`) integration: contribution check, private repo creation, Draft PRs, and publish."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from .guard import scan_directory_for_secrets


def get_gh_username() -> str:
    res = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, check=False)
    if res.returncode != 0 or not res.stdout.strip():
        raise RuntimeError("GitHub CLI (`gh`) is not authenticated. Run `gh auth login`.")
    return res.stdout.strip()


def get_today_contribution_count(username: str | None = None, tz_offset: str = "+03:00") -> int:
    """Query GitHub GraphQL API for today's contribution count."""
    user = username or get_gh_username()
    today = dt.date.today().isoformat()
    query = (
        f'{{ user(login: "{user}") {{ '
        f'contributionsCollection(from: "{today}T00:00:00{tz_offset}", to: "{today}T23:59:59{tz_offset}") {{ '
        f'contributionCalendar {{ weeks {{ contributionDays {{ date contributionCount }} }} }} '
        f'}} }} }}'
    )
    res = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"], capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "Failed to query GitHub contributions.")
    data = json.loads(res.stdout)
    weeks = (
        data.get("data", {})
        .get("user", {})
        .get("contributionsCollection", {})
        .get("contributionCalendar", {})
        .get("weeks", [])
    )
    total = 0
    for w in weeks:
        for d in w.get("contributionDays", []):
            if d.get("date") == today:
                total += int(d.get("contributionCount", 0))
    return total


def create_private_repo(repo_dir: str | Path, repo_name: str, description: str) -> str:
    """Create a PRIVATE GitHub repository and push `main`."""
    res = subprocess.run(
        [
            "gh", "repo", "create", repo_name,
            "--private",
            "--description", description,
            "--source", str(repo_dir),
            "--remote", "origin",
            "--push",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"Failed to create private repo {repo_name}")
    return res.stdout.strip()


def create_draft_pr(repo_dir: str | Path, title: str, body: str, base_branch: str = "main") -> str:
    """Open a Draft Pull Request for protected repositories (never direct commit)."""
    res = subprocess.run(
        [
            "gh", "pr", "create",
            "--draft",
            "--base", base_branch,
            "--title", title,
            "--body", body,
        ],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "Failed to create Draft PR.")
    return res.stdout.strip()


def publish_repo_safely(repo_dir: str | Path, repo_slug: str | None = None) -> str:
    """Run strict secret scan on a local repo before flipping its visibility to public."""
    findings = scan_directory_for_secrets(repo_dir)
    if findings:
        details = ", ".join(f"{f['file']} ({f['rule']})" for f in findings[:5])
        raise RuntimeError(f"Refusing to publish: potential secrets detected: {details}")

    cmd = ["gh", "repo", "edit"]
    if repo_slug:
        cmd.append(repo_slug)
    cmd.extend(["--visibility", "public", "--accept-visibility-change-consequences"])
    res = subprocess.run(cmd, cwd=str(repo_dir), capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "Failed to change repository visibility to public.")
    return f"Repository {repo_slug or Path(repo_dir).name} is now PUBLIC."
