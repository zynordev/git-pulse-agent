"""Multi-host project discovery: scans local and SSH hosts for git repos and unversioned projects."""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig, HostConfig
from .guard import is_protected_repo

CODE_EXTENSIONS = {".py", ".sh", ".js", ".ts", ".tsx", ".qml", ".go", ".rs", ".c", ".cpp", ".h", ".lua"}
PROJECT_MARKERS = {"pyproject.toml", "package.json", "Cargo.toml", "Makefile", "CMakeLists.txt", "Dockerfile", "go.mod"}


@dataclass
class DiscoveredProject:
    host: str
    mode: str  # "local" or "ssh"
    ssh_target: str
    path: str
    name: str
    is_git: bool
    remote_url: str = ""
    branch: str = ""
    is_protected: bool = False
    dirty: bool = False
    code_file_count: int = 0
    last_mtime: float = 0.0


def run_host_command(host: HostConfig, command: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    """Run a bash command either locally or on an SSH host."""
    if host.mode == "ssh":
        ssh_cmd = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            host.ssh_target,
            "bash", "-lc", command,
        ]
        return subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return subprocess.run(["bash", "-lc", command], capture_output=True, text=True, timeout=timeout, check=False)


def ensure_host_reachable(host: HostConfig) -> bool:
    """Verify SSH host connectivity, optionally invoking wake_command (e.g. WoL) once."""
    if host.mode != "ssh":
        return True
    ping = run_host_command(host, "echo ok", timeout=8)
    if ping.returncode == 0 and "ok" in ping.stdout:
        return True

    if host.wake_command:
        subprocess.run(host.wake_command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        for _ in range(6):
            time.sleep(3)
            ping = run_host_command(host, "echo ok", timeout=6)
            if ping.returncode == 0 and "ok" in ping.stdout:
                return True
    return False


def _scan_local_root(
    root_path: Path,
    host: HostConfig,
    cfg: AppConfig,
) -> list[DiscoveredProject]:
    projects: list[DiscoveredProject] = []
    if not root_path.is_dir():
        return projects

    ignore_set = set(cfg.ignore_dir_names)
    try:
        entries = sorted(root_path.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return projects

    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            continue
        if entry.name.startswith(".") or entry.name in ignore_set:
            continue

        git_dir = entry / ".git"
        if git_dir.is_dir():
            proj = _inspect_local_git_repo(entry, host, cfg)
            if proj:
                projects.append(proj)
        else:
            proj = _inspect_local_unversioned_dir(entry, host, cfg)
            if proj:
                projects.append(proj)
    return projects


def _inspect_local_git_repo(repo_dir: Path, host: HostConfig, cfg: AppConfig) -> DiscoveredProject | None:
    remote = subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
    )
    mtime_str = subprocess.run(
        ["git", "-C", str(repo_dir), "log", "-1", "--format=%ct"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    last_mtime = float(mtime_str) if mtime_str.isdigit() else repo_dir.stat().st_mtime

    # Skip vendored/external repos if github_user is set and remote doesn't belong to user
    if cfg.github_user and remote and cfg.github_user.lower() not in remote.lower():
        return None

    protected = is_protected_repo(repo_dir.name, str(repo_dir), remote, cfg.protected_pr_only_patterns)
    return DiscoveredProject(
        host=host.name,
        mode="local",
        ssh_target="",
        path=str(repo_dir),
        name=repo_dir.name,
        is_git=True,
        remote_url=remote,
        branch=branch,
        is_protected=protected,
        dirty=dirty,
        last_mtime=last_mtime,
    )


def _inspect_local_unversioned_dir(dir_path: Path, host: HostConfig, cfg: AppConfig) -> DiscoveredProject | None:
    code_files = 0
    has_marker = False
    latest_mtime = 0.0
    ignore_set = set(cfg.ignore_dir_names)

    for root, dirs, files in os.walk(dir_path):
        dirs[:] = [d for d in dirs if d not in ignore_set and not d.startswith(".")]
        depth = len(Path(root).relative_to(dir_path).parts)
        if depth > 2:
            dirs[:] = []
            continue
        for fname in files:
            if fname in PROJECT_MARKERS:
                has_marker = True
            if Path(fname).suffix.lower() in CODE_EXTENSIONS:
                code_files += 1
                try:
                    latest_mtime = max(latest_mtime, (Path(root) / fname).stat().st_mtime)
                except OSError:
                    pass

    if code_files >= 2 or (has_marker and code_files >= 1):
        protected = is_protected_repo(dir_path.name, str(dir_path), "", cfg.protected_pr_only_patterns)
        return DiscoveredProject(
            host=host.name,
            mode="local",
            ssh_target="",
            path=str(dir_path),
            name=dir_path.name,
            is_git=False,
            is_protected=protected,
            code_file_count=code_files,
            last_mtime=latest_mtime or dir_path.stat().st_mtime,
        )
    return None


def _scan_ssh_host(host: HostConfig, cfg: AppConfig) -> list[DiscoveredProject]:
    """Scan an SSH host in a single remote Python invocation for speed."""
    if not ensure_host_reachable(host):
        return []

    roots_repr = repr(host.scan_roots)
    ignore_repr = repr(cfg.ignore_dir_names)
    gh_user_repr = repr(cfg.github_user)
    remote_py = f"""
import json, os, subprocess
from pathlib import Path

roots = {roots_repr}
ignore = set({ignore_repr})
gh_user = ({gh_user_repr} or "").lower()
code_ext = {{".py", ".sh", ".js", ".ts", ".tsx", ".qml", ".go", ".rs", ".c", ".cpp", ".h", ".lua"}}
markers = {{"pyproject.toml", "package.json", "Cargo.toml", "Makefile", "CMakeLists.txt", "Dockerfile", "go.mod"}}
out = []

for r in roots:
    rp = Path(os.path.expanduser(r))
    if not rp.is_dir():
        continue
    for entry in sorted(rp.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir() or entry.is_symlink() or entry.name.startswith(".") or entry.name in ignore:
            continue
        if (entry / ".git").is_dir():
            rem = subprocess.run(["git", "-C", str(entry), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
            if gh_user and rem and gh_user not in rem.lower():
                continue
            br = subprocess.run(["git", "-C", str(entry), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
            dirty = bool(subprocess.run(["git", "-C", str(entry), "status", "--porcelain"], capture_output=True, text=True).stdout.strip())
            mt = subprocess.run(["git", "-C", str(entry), "log", "-1", "--format=%ct"], capture_output=True, text=True).stdout.strip()
            out.append({{"path": str(entry), "name": entry.name, "is_git": True, "remote": rem, "branch": br, "dirty": dirty, "mtime": float(mt) if mt.isdigit() else entry.stat().st_mtime, "code_files": 0}})
        else:
            cf = 0
            hm = False
            lm = 0.0
            for root, dirs, files in os.walk(entry):
                dirs[:] = [d for d in dirs if d not in ignore and not d.startswith(".")]
                if len(Path(root).relative_to(entry).parts) > 2:
                    dirs[:] = []
                    continue
                for f in files:
                    if f in markers: hm = True
                    if Path(f).suffix.lower() in code_ext:
                        cf += 1
                        try: lm = max(lm, (Path(root) / f).stat().st_mtime)
                        except OSError: pass
            if cf >= 2 or (hm and cf >= 1):
                out.append({{"path": str(entry), "name": entry.name, "is_git": False, "remote": "", "branch": "", "dirty": False, "mtime": lm or entry.stat().st_mtime, "code_files": cf}})
print(json.dumps(out))
"""
    res = run_host_command(host, f"python3 -c {shlex.quote(remote_py)}", timeout=25)
    if res.returncode != 0 or not res.stdout.strip():
        return []

    import json
    try:
        items = json.loads(res.stdout.strip().splitlines()[-1])
    except Exception:
        return []

    projects: list[DiscoveredProject] = []
    for it in items:
        protected = is_protected_repo(it["name"], it["path"], it["remote"], cfg.protected_pr_only_patterns)
        projects.append(
            DiscoveredProject(
                host=host.name,
                mode="ssh",
                ssh_target=host.ssh_target,
                path=it["path"],
                name=it["name"],
                is_git=bool(it["is_git"]),
                remote_url=it.get("remote", ""),
                branch=it.get("branch", ""),
                is_protected=protected,
                dirty=bool(it.get("dirty", False)),
                code_file_count=int(it.get("code_files", 0)),
                last_mtime=float(it.get("mtime", 0.0)),
            )
        )
    return projects


def discover_all_projects(cfg: AppConfig) -> list[DiscoveredProject]:
    """Discover git repos and unversioned projects across all configured hosts."""
    all_projects: list[DiscoveredProject] = []
    for host in cfg.hosts:
        if host.mode == "ssh":
            all_projects.extend(_scan_ssh_host(host, cfg))
        else:
            for root_str in host.scan_roots:
                all_projects.extend(_scan_local_root(Path(os.path.expanduser(root_str)), host, cfg))
    all_projects.sort(key=lambda p: p.last_mtime, reverse=True)
    return all_projects
