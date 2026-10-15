#!/usr/bin/env python3
"""PostToolUseFailure durable trace: every failed tool call leaves a line on disk.

"Every failure leaves a durable trace" as a mechanism instead of a resolution. One JSON
line per failure, {ts, cwd, tool, input, error}, so a week later the question "what keeps
failing" is answered with grep, not memory. Secrets are redacted before anything is cut.

Default file: <state>/tool-failures.jsonl, or CLAUDE_GATE_TRACE_FILE.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402

NAME = "trace"


def trace_path():
    return os.path.expanduser(os.environ.get("CLAUDE_GATE_TRACE_FILE")
                              or os.path.join(_gate.state_dir(), "tool-failures.jsonl"))


def record(event):
    """-> the row written, or None. Never raises."""
    try:
        if _gate.gate_off(NAME):
            return None
        row = {"ts": _gate.now_iso(), "cwd": event.get("cwd"), "tool": event.get("tool_name"),
               "input": _gate.redact(event.get("tool_input"), 300),
               "error": _gate.redact(event.get("error") or event.get("tool_response") or "", 500)}
        if _gate.append_private(trace_path(), json.dumps(row, ensure_ascii=False) + "\n"):
            return row
        return None
    except Exception:
        return None


if __name__ == "__main__":
    try:
        ev = _gate.read_event()
        if ev is not None:
            record(ev)
    except Exception:
        pass
    sys.exit(0)
