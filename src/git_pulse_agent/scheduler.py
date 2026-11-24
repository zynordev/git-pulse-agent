"""systemd --user timer installer/uninstaller for daily scheduled execution."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_NAME = "git-pulse-agent.service"
TIMER_NAME = "git-pulse-agent.timer"


def render_service_unit(exec_cmd: str) -> str:
    return (
        "[Unit]\n"
        "Description=git-pulse-agent Daily Multi-Host Code Audit & Streak Guardian\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={exec_cmd}\n"
        "Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin\n"
    )


def render_timer_unit(schedule_time: str = "15:00:00") -> str:
    return (
        "[Unit]\n"
        "Description=Run git-pulse-agent daily\n\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {schedule_time}\n"
        "Persistent=true\n"
        "RandomizedDelaySec=120\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def install_systemd_timer(schedule_time: str = "15:00:00", git_pulse_bin: str | None = None) -> tuple[Path, Path]:
    """Install and enable the systemd --user service and daily timer."""
    exe = git_pulse_bin or shutil.which("git-pulse") or f"{sys.executable} -m git_pulse_agent.cli"
    exec_cmd = f"{exe} run"

    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    service_path = SYSTEMD_USER_DIR / SERVICE_NAME
    timer_path = SYSTEMD_USER_DIR / TIMER_NAME

    service_path.write_text(render_service_unit(exec_cmd), encoding="utf-8")
    timer_path.write_text(render_timer_unit(schedule_time), encoding="utf-8")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", TIMER_NAME], check=True)
    return service_path, timer_path


def uninstall_systemd_timer() -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", TIMER_NAME], check=False)
    for fname in (TIMER_NAME, SERVICE_NAME):
        p = SYSTEMD_USER_DIR / fname
        p.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
