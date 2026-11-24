"""Configuration loader and defaults for git-pulse-agent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "git-pulse-agent"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "git-pulse-agent"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_LOGS_DIR = DATA_DIR / "logs"
DEFAULT_STATE_PATH = DATA_DIR / "state.json"


@dataclass
class HostConfig:
    name: str
    mode: str = "local"  # "local" or "ssh"
    ssh_target: str = ""
    wake_command: str = ""
    scan_roots: list[str] = field(default_factory=list)
    max_depth: int = 2


@dataclass
class AppConfig:
    github_user: str = ""
    schedule_time: str = "15:00:00"
    skip_if_already_committed_today: bool = True
    max_repos_to_audit: int = 5
    agy_bin: str = "agy"
    preferred_models: list[str] = field(default_factory=list)
    agy_timeout_seconds: int = 600
    protected_pr_only_patterns: list[str] = field(
        default_factory=lambda: ["research-", "thesis-", "competition-"]
    )
    ignore_dir_names: list[str] = field(
        default_factory=lambda: [
            ".git", ".cache", ".config", ".local", ".venv", "venv",
            "node_modules", "__pycache__", "dist", "build", "target",
            "Downloads", "Desktop", "Pictures", "Videos", "Music",
            "Obsidian", "Android", "Sync", "Trash",
        ]
    )
    hosts: list[HostConfig] = field(default_factory=list)
    notify_desktop: bool = True
    notify_command: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppConfig":
        hosts = [
            HostConfig(
                name=h.get("name", "local"),
                mode=h.get("mode", "local"),
                ssh_target=h.get("ssh_target", ""),
                wake_command=h.get("wake_command", ""),
                scan_roots=list(h.get("scan_roots", [])),
                max_depth=int(h.get("max_depth", 2)),
            )
            for h in raw.get("hosts", [])
        ]
        defaults = cls()
        return cls(
            github_user=raw.get("github_user", defaults.github_user),
            schedule_time=raw.get("schedule_time", defaults.schedule_time),
            skip_if_already_committed_today=bool(
                raw.get("skip_if_already_committed_today", defaults.skip_if_already_committed_today)
            ),
            max_repos_to_audit=int(raw.get("max_repos_to_audit", defaults.max_repos_to_audit)),
            agy_bin=raw.get("agy_bin", defaults.agy_bin),
            preferred_models=list(raw.get("preferred_models", defaults.preferred_models)),
            agy_timeout_seconds=int(raw.get("agy_timeout_seconds", defaults.agy_timeout_seconds)),
            protected_pr_only_patterns=list(
                raw.get("protected_pr_only_patterns", defaults.protected_pr_only_patterns)
            ),
            ignore_dir_names=list(raw.get("ignore_dir_names", defaults.ignore_dir_names)),
            hosts=hosts,
            notify_desktop=bool(raw.get("notify_desktop", defaults.notify_desktop)),
            notify_command=raw.get("notify_command", defaults.notify_command),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "github_user": self.github_user,
            "schedule_time": self.schedule_time,
            "skip_if_already_committed_today": self.skip_if_already_committed_today,
            "max_repos_to_audit": self.max_repos_to_audit,
            "agy_bin": self.agy_bin,
            "preferred_models": self.preferred_models,
            "agy_timeout_seconds": self.agy_timeout_seconds,
            "protected_pr_only_patterns": self.protected_pr_only_patterns,
            "ignore_dir_names": self.ignore_dir_names,
            "hosts": [
                {
                    "name": h.name,
                    "mode": h.mode,
                    "ssh_target": h.ssh_target,
                    "wake_command": h.wake_command,
                    "scan_roots": h.scan_roots,
                    "max_depth": h.max_depth,
                }
                for h in self.hosts
            ],
            "notify_desktop": self.notify_desktop,
            "notify_command": self.notify_command,
        }


def load_config(path: str | Path | None = None) -> AppConfig:
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    if not target.is_file():
        return AppConfig(
            hosts=[HostConfig(name="local", mode="local", scan_roots=[str(Path.home())], max_depth=2)]
        )
    raw = json.loads(target.read_text(encoding="utf-8"))
    return AppConfig.from_dict(raw)


def save_config(cfg: AppConfig, path: str | Path | None = None) -> Path:
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
