#!/usr/bin/env python3
"""PreToolUse deny list for Bash: three narrow traps, each denial teaches the right form.

The list is short on purpose. Every rule names a command that LOOKS like it worked while
doing damage or returning a false answer, and every reason says what to run instead, so
the model's retry is the correct command rather than a guess.

Exit code does not block a tool call here; the deny JSON does. Anything unexpected
(bad stdin, a tool that is not Bash, an exception) exits 0 with no output: allow.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402

NAME = "deny"

RULES = [
    ("rm",
     re.compile(r"(?:^|[;&|(]|\$\(|`)\s*rm\s+(?!-h\b)"),
     lambda c: not re.search(r"\b(?:git|npm|pnpm|yarn|docker|apt(?:-get)?)\s+rm\b", c),
     "Use `trash <path>` instead of rm: a trashed file can be restored, a removed one cannot "
     "(trash-cli on Linux, `trash` on macOS). git/npm/docker/apt rm are exempt; if this "
     "pattern caught a legitimate command, rephrase it."),
    ("journalctl-today",
     re.compile(r"journalctl.*--since[= ][\"']?today\s+\d"),
     lambda c: True,
     "`--since \"today HH:MM\"` is not a form every systemd accepts, and when it fails the "
     "output reads as a clean zero events. Use `--since \"6 hours ago\"` or a full "
     "`YYYY-MM-DD HH:MM:SS` timestamp."),
    ("interop-stderr",
     re.compile(r"/mnt/c/.*2>\s*/dev/null"),
     lambda c: True,
     "Never silence stderr on a Windows-interop call (/mnt/c/...): a missing binary returns "
     "an empty result that reads as a real answer. Drop the 2>/dev/null."),
]


def decide(event):
    """-> (rule_name, reason) for a command to deny, or None. Never raises."""
    try:
        if event.get("tool_name") != "Bash" or _gate.gate_off(NAME):
            return None
        cmd = str((event.get("tool_input") or {}).get("command") or "")
        for name, rx, extra, reason in RULES:
            if rx.search(cmd) and extra(cmd):
                return name, reason
        return None
    except Exception:
        return None


def main():
    try:
        ev = _gate.read_event()
        hit = decide(ev) if ev is not None else None
        if hit:
            _gate.log_bounce(NAME, ev, rule=hit[0])
            _gate.emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                               "permissionDecision": "deny",
                                               "permissionDecisionReason": hit[1]}})
    except SystemExit:
        raise
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
