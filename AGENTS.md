# AGENTS.md — git-pulse-agent

## Overview
`git-pulse-agent` is a zero-dependency Python 3.10+ daemon/CLI that audits projects across local and SSH-connected hosts (e.g. Raspberry Pi + Laptop) on a daily schedule using Antigravity CLI (`agy`).

## Architecture
- `src/git_pulse_agent/config.py`: Config schema, host definitions, protected (PR-only) patterns, and secret-ignore rules.
- `src/git_pulse_agent/scanner.py`: Local + SSH project discovery (existing git repos + unversioned local projects).
- `src/git_pulse_agent/guard.py`: Protected-repo (PR-only) policy enforcement + strict secret/privacy scanner.
- `src/git_pulse_agent/github.py`: `gh` CLI wrapper (today's contribution count, private repo creation, Draft PR creation, safe public promotion).
- `src/git_pulse_agent/agy_runner.py`: Dynamic `agy models` probing, fallback selection, and non-interactive audit execution.
- `src/git_pulse_agent/engine.py`: Daily orchestration pipeline (check streak -> audit repos -> PR-only on protected repos -> fallback promote unversioned project to private repo -> log & notify).
- `src/git_pulse_agent/scheduler.py`: `systemd --user` timer installer/uninstaller (default daily 15:00).
- `src/git_pulse_agent/cli.py`: `git-pulse` CLI entrypoint.

## Safety Rules
1. **Zero personal data in repo:** Hostnames, SSH aliases, personal directories, and tokens live strictly in `~/.config/git-pulse-agent/config.json`.
2. **Protected repos are strictly PR-only:** Never commit directly to protected repos' default/active branches; always create an `audit/*` branch and open a Draft PR.
3. **New repos are ALWAYS private:** Unversioned projects discovered automatically are always created with `--private` and require explicit user approval (`git-pulse publish <repo>`) to become public.
4. **Tests:** Run `PYTHONPATH=src python3 -m unittest discover -s tests -v` before committing.
