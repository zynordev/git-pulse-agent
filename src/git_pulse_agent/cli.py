"""Command-line interface for git-pulse-agent (`git-pulse`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .agy_runner import list_available_models
from .config import DEFAULT_CONFIG_PATH, load_config, save_config
from .engine import run_daily_pulse
from .github import get_gh_username, get_today_contribution_count, publish_repo_safely
from .guard import scan_directory_for_secrets
from .scanner import discover_all_projects
from .scheduler import install_systemd_timer, uninstall_systemd_timer


def cmd_status(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    user = cfg.github_user or get_gh_username()
    count = get_today_contribution_count(user)
    models = list_available_models(cfg.agy_bin)
    print(f"GitHub User      : {user}")
    print(f"Today's Commits  : {count} ({'GREEN' if count > 0 else 'EMPTY'})")
    print(f"Config Path      : {args.config or DEFAULT_CONFIG_PATH}")
    print(f"Configured Hosts : {', '.join(h.name for h in cfg.hosts) or 'none'}")
    print(f"Available Models : {len(models)} ({', '.join(models[:4])}{'...' if len(models) > 4 else ''})")


def cmd_scan(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    projects = discover_all_projects(cfg)
    if args.json:
        print(json.dumps([p.__dict__ for p in projects], ensure_ascii=False, indent=2))
        return
    git_repos = [p for p in projects if p.is_git]
    unversioned = [p for p in projects if not p.is_git]
    print(f"=== Git Repositories ({len(git_repos)}) ===")
    for p in git_repos:
        flags = []
        if p.is_protected:
            flags.append("PR-ONLY")
        if p.dirty:
            flags.append("DIRTY")
        tag = f" [{' | '.join(flags)}]" if flags else ""
        print(f"  - [{p.host}] {p.name} ({p.branch}) -> {p.path}{tag}")
    print(f"\n=== Unversioned Projects ({len(unversioned)}) ===")
    for p in unversioned:
        print(f"  - [{p.host}] {p.name} ({p.code_file_count} code files) -> {p.path}")


def cmd_run(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    summary = run_daily_pulse(cfg, force=args.force, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))
        return
    print(f"[{summary.date}] Action : {summary.action_taken}")
    if summary.target_project:
        print(f"Target : {summary.target_project} ({summary.host})")
    if summary.model_used:
        print(f"Model  : {summary.model_used}")
    print(f"Details: {summary.details}")
    if summary.log_path:
        print(f"Log    : {summary.log_path}")


def cmd_publish(args: argparse.Namespace) -> None:
    repo_dir = Path(args.repo_dir).resolve()
    msg = publish_repo_safely(repo_dir, repo_slug=args.slug)
    print(msg)


def cmd_secret_scan(args: argparse.Namespace) -> None:
    findings = scan_directory_for_secrets(args.directory)
    if not findings:
        print("CLEAN: 0 potential secrets detected.")
        return
    print(f"WARNING: {len(findings)} potential secret(s) detected:")
    for f in findings:
        print(f"  - {f['file']} ({f['rule']})")
    sys.exit(2)


def cmd_install_timer(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    schedule = args.time or cfg.schedule_time
    svc, tmr = install_systemd_timer(schedule_time=schedule)
    print(f"Installed systemd service : {svc}")
    print(f"Installed systemd timer   : {tmr} (daily at {schedule})")


def cmd_uninstall_timer(args: argparse.Namespace) -> None:
    uninstall_systemd_timer()
    print("Uninstalled git-pulse-agent systemd user timer.")


def cmd_init_config(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    saved = save_config(cfg, args.config)
    print(f"Saved config template to {saved}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-pulse",
        description="Automated multi-host code auditor, private repo promoter, and GitHub streak guardian.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", help="Path to config.json (default: ~/.config/git-pulse-agent/config.json)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_st = sub.add_parser("status", help="Show today's GitHub streak status and available agy models")
    p_st.set_defaults(func=cmd_status)

    p_sc = sub.add_parser("scan", help="Discover git repos and unversioned projects across configured hosts")
    p_sc.add_argument("--json", action="store_true")
    p_sc.set_defaults(func=cmd_scan)

    p_run = sub.add_parser("run", help="Run the daily audit / commit / private repo promotion pipeline")
    p_run.add_argument("--force", action="store_true", help="Run even if GitHub already has commits today")
    p_run.add_argument("--dry-run", action="store_true", help="Discover and plan without running agy or modifying git")
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_pub = sub.add_parser("publish", help="Secret-scan a private repo and flip it to public after user review")
    p_pub.add_argument("repo_dir", help="Local path to the repository")
    p_pub.add_argument("--slug", help="Optional GitHub repo slug (owner/name)")
    p_pub.set_defaults(func=cmd_publish)

    p_sec = sub.add_parser("secret-scan", help="Scan a directory for hardcoded secrets or sensitive files")
    p_sec.add_argument("directory", help="Directory path to scan")
    p_sec.set_defaults(func=cmd_secret_scan)

    p_inst = sub.add_parser("install-timer", help="Install & enable daily systemd user timer (default 15:00)")
    p_inst.add_argument("--time", help="Wall-clock time HH:MM:SS (default from config: 15:00:00)")
    p_inst.set_defaults(func=cmd_install_timer)

    p_uninst = sub.add_parser("uninstall-timer", help="Disable & remove systemd user timer")
    p_uninst.set_defaults(func=cmd_uninstall_timer)

    p_init = sub.add_parser("init-config", help="Write default config.json to ~/.config/git-pulse-agent/config.json")
    p_init.set_defaults(func=cmd_init_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
