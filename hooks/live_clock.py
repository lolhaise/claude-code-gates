#!/usr/bin/env python3
"""UserPromptSubmit live clock: the model gets the real time on every turn.

A model has no felt sense of time. Without an anchor it narrates "last night" for a
build that finished two hours ago and treats a stale note as today's news. This injects
one line per prompt, computed fresh, with how long the session has been running:

    [live-clock] Thursday 2026-10-15 19:02 UTC · session started 18:40 (22m in)

Uses the local timezone (set TZ to override). Runs in milliseconds; it blocks the turn,
so it stays tiny and exits 0 on any error with nothing injected.
"""
import calendar
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402

NAME = "clock"


def session_age(transcript_path, now=None):
    """' . session started HH:MM (XhYYm in)' off the transcript's first timestamp, or ''."""
    try:
        if not transcript_path or not os.path.exists(transcript_path):
            return ""
        ts = ""
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            for _ in range(20):          # the first line can be a title row with no timestamp
                line = f.readline(200000)
                if not line:
                    break
                try:
                    ts = json.loads(line).get("timestamp") or ""
                except Exception:
                    continue
                if ts:
                    break
        if not ts:
            return ""
        # transcript stamps are UTC; timegm parses the struct AS UTC
        t0 = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
        now = time.time() if now is None else now
        mins = max(0, int((now - t0) / 60))
        started = time.strftime("%H:%M", time.localtime(t0))
        if time.localtime(t0)[:3] != time.localtime(now)[:3]:
            started = time.strftime("%a %H:%M", time.localtime(t0))
        age = ("%dh%02dm" % (mins // 60, mins % 60)) if mins >= 60 else ("%dm" % mins)
        return " · session started %s (%s in)" % (started, age)
    except Exception:
        return ""


def clock_line(event, now=None):
    now = time.time() if now is None else now
    return ("[live-clock] " + time.strftime("%A %Y-%m-%d %H:%M %Z", time.localtime(now))
            + session_age((event or {}).get("transcript_path"), now))


if __name__ == "__main__":
    try:
        if not _gate.gate_off(NAME):
            ev = _gate.read_event() or {}
            _gate.emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                               "additionalContext": clock_line(ev)}})
    except SystemExit:
        raise
    except Exception:
        pass
    sys.exit(0)
