#!/usr/bin/env python3
"""StopFailure alerter: a turn that dies on an API error becomes a ledger row and an alert.

Claude Code fires StopFailure INSTEAD of Stop when a turn ends on an API error
(rate_limit, overloaded, authentication_failed, billing_error, server_error,
max_output_tokens, ...). Its output is ignored, so this hook can only observe, and
without it an eaten turn surfaces hours later, if at all.

Four properties, each a decision:
  1. LEDGER FIRST, never gated on the alert. The row is written before the kill switch is
     read and before any post, so a switched-off, suppressed or failed alert still leaves
     a row. Switching alerts off never switches observability off.
  2. ONE ALERT PER (profile, error) PER 15 MINUTES, failing open. The window is stamped
     only after a delivery succeeds, and suppressed alerts are counted, so a burst of 40
     rate limits never reads as one.
  3. A WEBHOOK POST RUNS DETACHED. The event fires as the session ends, the worst case for
     async work, so the post goes to a child in its own session that outlives teardown.
  4. REDACT BEFORE TRUNCATE on every field.

Notifier (first one configured wins):
  CLAUDE_GATES_WEBHOOK_URL=https://...   POST {"text": ..., "content": ...}
  CLAUDE_GATES_NOTIFY_FILE=~/alerts.log  append one line
  neither                                print the alert to stdout
Ledger: <state>/turn-failures.jsonl
"""
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402

NAME = "stopfail"
EVENT = "StopFailure"
WINDOW_S = 15 * 60
LEDGER_MAX = 300
BODY_MAX = 200


def ledger_path():
    return os.path.join(_gate.state_dir(), "turn-failures.jsonl")


def now():
    v = os.environ.get("CLAUDE_GATE_STOPFAIL_NOW")      # tests pin the clock
    try:
        return float(v) if v else time.time()
    except ValueError:
        return time.time()


def profile():
    """Which Claude Code config dir this session runs under, for people who run several."""
    d = os.environ.get("CLAUDE_CONFIG_DIR", "").rstrip("/")
    return os.path.basename(d) if d else "default"


def build_body(error, details, session_id):
    lines = ["[turn failed] %s . profile %s" % (error, profile())]
    d = " ".join(_gate.redact(details).split())[:BODY_MAX]
    if d:
        lines.append(d)
    lines.append("session %s" % (_gate.redact(session_id)[:8] or "unknown"))
    return "\n".join(lines)


def handle(event):
    """-> disposition string. Never raises."""
    try:
        if event.get("hook_event_name") not in (EVENT, None):
            return "wrong-event"
        ts = now()
        error = " ".join(_gate.redact(event.get("error")).split())[:60] or "unknown"
        sid = _gate.redact(event.get("session_id"))
        key = "%s|%s" % (profile(), error)
        alert_id = hashlib.sha256(("%s|%s|%s|%d" % (key, sid, ts, os.getpid())).encode()).hexdigest()[:12]
        _gate.append_private(ledger_path(), json.dumps({
            "ts": _gate.now_iso(), "event": "turn-failure", "alert_id": alert_id,
            "profile": profile(), "session_id": sid, "error": error,
            "error_details": _gate.redact(event.get("error_details"), LEDGER_MAX),
            "last_assistant_message": _gate.redact(event.get("last_assistant_message"), LEDGER_MAX),
            "transcript_path": str(event.get("transcript_path") or ""),
            "cwd": str(event.get("cwd") or ""),
        }, ensure_ascii=False) + "\n")
        if _gate.gate_off(NAME):
            disp = "off"
        else:
            disp = _gate.alert(build_body(error, event.get("error_details"), sid), key,
                               "stopfail-rate.json", WINDOW_S, allow_stdout=True, ts=ts)
        _gate.append_private(ledger_path(), json.dumps(
            {"ts": _gate.now_iso(), "event": "turn-failure-alert", "alert_id": alert_id,
             "result": disp}) + "\n")
        return disp
    except Exception as e:
        return "exception:%s" % type(e).__name__


if __name__ == "__main__":
    try:
        ev = _gate.read_event()
        if ev is not None:
            handle(ev)
    except Exception:
        pass
    sys.exit(0)
