"""Core daily orchestration engine: streak check -> audit -> PR-only guard -> private repo promotion."""

from __future__ import annotations

import datetime as dt
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .agy_runner import run_agy_prompt
from .config import DEFAULT_LOGS_DIR, AppConfig
from .github import (
    create_draft_pr,
    create_private_repo,
    get_gh_username,
    get_today_contribution_count,
)
from .guard import generate_safe_gitignore, scan_directory_for_secrets
from .scanner import DiscoveredProject, discover_all_projects


@dataclass
class RunSummary:
    date: str
    action_taken: str  # "skipped_already_green", "committed", "draft_pr", "promoted_private", "dry_run", "no_action"
    target_project: str = ""
    host: str = ""
    model_used: str = ""
    details: str = ""
    log_path: str = ""


def build_audit_prompt(project: DiscoveredProject) -> str:
    """Build a strict, high-signal audit prompt for `agy`."""
    if project.is_protected:
        return (
            f"You are auditing a PROTECTED research repository '{project.name}' at {project.path}.\n"
            "STRICT RULES:\n"
            "1. Inspect the codebase, docs, and tests for genuine inconsistencies, bugs, broken links, or missing edge-case tests.\n"
            "2. Do NOT make trivial/cosmetic/junk edits. If nothing genuinely needs fixing, make ZERO edits and reply 'NO_ACTION_NEEDED'.\n"
            "3. Do NOT run git commit or git push yourself — only edit files and verify tests pass.\n"
            "4. Summarize your findings concisely in 3-5 bullet points."
        )
    return (
        f"You are auditing the project '{project.name}' at {project.path}.\n"
        "STRICT RULES:\n"
        "1. Review the code, scripts, error handling, tests, and documentation for ONE concrete, genuine improvement "
        "(a real bug fix, missing test, input validation, CLI ergonomics, or missing doc).\n"
        "2. Do NOT make junk, whitespace-only, or fake edits. If the project is already clean and nothing meaningful can be improved, "
        "make ZERO edits and reply 'NO_ACTION_NEEDED'.\n"
        "3. Run existing tests or syntax checks to verify nothing is broken.\n"
        "4. Do NOT run git commit or git push — only apply and verify the change, then output a 1-line conventional commit title on the first line prefixed with 'COMMIT_MSG: '."
    )


def _extract_commit_msg(agy_output: str, default_msg: str) -> str:
    for line in agy_output.splitlines():
        if line.strip().startswith("COMMIT_MSG:"):
            msg = line.split(":", 1)[1].strip()
            if msg:
                return msg[:100]
    return default_msg


def _has_local_changes(repo_dir: str | Path) -> bool:
    res = subprocess.run(
        ["git", "-C", str(repo_dir), "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    )
    return bool(res.stdout.strip())


def _audit_local_repo(project: DiscoveredProject, cfg: AppConfig) -> RunSummary | None:
    today = dt.date.today().isoformat()
    if project.dirty:
        return None  # Never touch repos with uncommitted WIP changes

    # Always fetch first so we never work on stale history
    subprocess.run(["git", "-C", project.path, "fetch", "origin"], capture_output=True, check=False)

    base_branch = project.branch or "main"
    audit_branch = f"audit/{today}-{project.name[:20]}"

    if project.is_protected:
        # Protected repos (e.g. TÜBİTAK): isolate on a new branch, NEVER commit to base branch
        br_res = subprocess.run(
            ["git", "-C", project.path, "checkout", "-b", audit_branch],
            capture_output=True, text=True, check=False,
        )
        if br_res.returncode != 0:
            return None

    agy_res = run_agy_prompt(
        build_audit_prompt(project),
        cwd=project.path,
        agy_bin=cfg.agy_bin,
        preferred_models=cfg.preferred_models,
        timeout_seconds=cfg.agy_timeout_seconds,
    )

    if not agy_res.ok or "NO_ACTION_NEEDED" in agy_res.output or not _has_local_changes(project.path):
        if project.is_protected:
            subprocess.run(["git", "-C", project.path, "checkout", base_branch], capture_output=True, check=False)
            subprocess.run(["git", "-C", project.path, "branch", "-D", audit_branch], capture_output=True, check=False)
        return None

    commit_msg = _extract_commit_msg(
        agy_res.output,
        f"chore(audit): automated quality improvement ({today})",
    )
    subprocess.run(["git", "-C", project.path, "add", "-A"], check=True)
    subprocess.run(["git", "-C", project.path, "commit", "-m", commit_msg], check=True)

    if project.is_protected:
        subprocess.run(["git", "-C", project.path, "push", "-u", "origin", audit_branch], check=True)
        pr_url = create_draft_pr(
            project.path,
            title=f"[Audit {today}] {commit_msg}",
            body=f"Automated consistency audit by `git-pulse-agent` using `{agy_res.model}`.\n\n### Findings\n{agy_res.output[:2000]}",
            base_branch=base_branch,
        )
        subprocess.run(["git", "-C", project.path, "checkout", base_branch], capture_output=True, check=False)
        return RunSummary(
            date=today,
            action_taken="draft_pr",
            target_project=project.name,
            host=project.host,
            model_used=agy_res.model,
            details=f"Protected repo — opened Draft PR instead of direct commit: {pr_url}",
        )

    subprocess.run(["git", "-C", project.path, "pull", "--rebase"], check=False)
    subprocess.run(["git", "-C", project.path, "push"], check=True)
    return RunSummary(
        date=today,
        action_taken="committed",
        target_project=project.name,
        host=project.host,
        model_used=agy_res.model,
        details=f"Committed & pushed: {commit_msg}",
    )


def _promote_unversioned_local_project(
    project: DiscoveredProject,
    cfg: AppConfig,
) -> RunSummary | None:
    """Initialize git, create a PRIVATE GitHub repo, and run an initial improvement pass."""
    today = dt.date.today().isoformat()
    if project.is_protected:
        return None

    secrets = scan_directory_for_secrets(project.path)
    if secrets:
        return None  # Skip directories containing hardcoded secrets/keys

    gitignore_path = Path(project.path) / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(generate_safe_gitignore(), encoding="utf-8")

    subprocess.run(["git", "-C", project.path, "init", "-b", "main"], check=True)
    subprocess.run(["git", "-C", project.path, "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", project.path, "commit", "-m", f"chore: initial import of {project.name}"],
        check=True,
    )

    repo_slug = f"{cfg.github_user}/{project.name}" if cfg.github_user else project.name
    create_private_repo(
        project.path,
        repo_name=repo_slug,
        description=f"Auto-archived private repository for {project.name} (promoted by git-pulse-agent)",
    )

    # Run one real improvement/documentation pass via agy
    agy_res = run_agy_prompt(
        build_audit_prompt(project),
        cwd=project.path,
        agy_bin=cfg.agy_bin,
        preferred_models=cfg.preferred_models,
        timeout_seconds=cfg.agy_timeout_seconds,
    )
    if agy_res.ok and _has_local_changes(project.path):
        commit_msg = _extract_commit_msg(agy_res.output, f"docs: add README and initial cleanup for {project.name}")
        subprocess.run(["git", "-C", project.path, "add", "-A"], check=True)
        subprocess.run(["git", "-C", project.path, "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "-C", project.path, "push"], check=True)

    return RunSummary(
        date=today,
        action_taken="promoted_private",
        target_project=project.name,
        host=project.host,
        model_used=agy_res.model if agy_res.ok else "none",
        details=f"Created PRIVATE GitHub repo `{repo_slug}`. Review and run `git-pulse publish {project.path}` if you want to make it public.",
    )


def _write_run_log(summary: RunSummary) -> Path:
    DEFAULT_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = DEFAULT_LOGS_DIR / f"{summary.date}.md"
    entry = (
        f"## {dt.datetime.now().strftime('%H:%M:%S')} — {summary.action_taken}\n"
        f"- **Target:** `{summary.target_project or '-'}` ({summary.host or 'local'})\n"
        f"- **Model:** `{summary.model_used or '-'}`\n"
        f"- **Details:** {summary.details}\n\n"
    )
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(entry)
    return log_file


def _send_notification(cfg: AppConfig, summary: RunSummary) -> None:
    msg = f"[git-pulse] {summary.action_taken}: {summary.target_project} — {summary.details}"
    if cfg.notify_desktop:
        subprocess.run(
            ["notify-send", "-a", "git-pulse-agent", "Git Pulse Daily Run", msg],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    if cfg.notify_command:
        cmd = cfg.notify_command.replace("{message}", shlex.quote(msg))
        subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def run_daily_pulse(
    cfg: AppConfig,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> RunSummary:
    """Execute the daily multi-host audit & streak-protection pipeline."""
    today = dt.date.today().isoformat()
    user = cfg.github_user or get_gh_username()
    cfg.github_user = user

    if not force and cfg.skip_if_already_committed_today:
        count = get_today_contribution_count(user)
        if count > 0:
            summary = RunSummary(
                date=today,
                action_taken="skipped_already_green",
                details=f"GitHub already has {count} contribution(s) today for `{user}`.",
            )
            summary.log_path = str(_write_run_log(summary))
            return summary

    projects = discover_all_projects(cfg)
    git_repos = [p for p in projects if p.is_git and p.mode == "local"]
    unversioned = [p for p in projects if not p.is_git and p.mode == "local"]

    if dry_run:
        lines = [
            f"Discovered {len(projects)} total projects across {len(cfg.hosts)} host(s).",
            f"Local git repos ready to audit: {len(git_repos)} (protected PR-only: {sum(1 for p in git_repos if p.is_protected)})",
            f"Local unversioned candidates for private repo promotion: {len(unversioned)}",
        ]
        summary = RunSummary(
            date=today,
            action_taken="dry_run",
            details=" | ".join(lines),
        )
        return summary

    # Phase 1: Audit existing git repos (regular repos first, then protected PR-only repos)
    ordered_repos = sorted(git_repos, key=lambda p: (p.is_protected, -p.last_mtime))
    for repo in ordered_repos[: cfg.max_repos_to_audit]:
        result = _audit_local_repo(repo, cfg)
        if result is not None:
            result.log_path = str(_write_run_log(result))
            _send_notification(cfg, result)
            return result

    # Phase 2: Fallback — promote an unversioned local project to a PRIVATE GitHub repo
    for cand in unversioned:
        result = _promote_unversioned_local_project(cand, cfg)
        if result is not None:
            result.log_path = str(_write_run_log(result))
            _send_notification(cfg, result)
            return result

    summary = RunSummary(
        date=today,
        action_taken="no_action",
        details="No actionable improvements or safe unversioned projects found.",
    )
    summary.log_path = str(_write_run_log(summary))
    return summary
