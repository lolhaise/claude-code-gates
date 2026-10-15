#!/usr/bin/env python3
"""Config tamper gate: a settings edit that removes or swaps a hook is blocked.

Every gate in this repo is a line in ~/.claude/settings.json. Anything that rewrites that
file mid-session (an upgrade script, a worker, a fat-fingered edit) can drop a tripwire
and nothing says so. Claude Code fires ConfigChange when a settings file changes during a
session and honours a block, so a removal becomes a blocked, ledgered, alerted event.

  * Baseline: the last ALLOWED hook set per settings file, captured on first sight.
  * Removed or altered handler (same event and matcher, different command) -> BLOCK.
    An alteration is worse than a removal: the tripwire is still declared but points
    somewhere else. Added hooks and timeout changes are allowed and become the baseline.
  * Deliberate removal: touch <state>/config-ALLOW (single use, 15 minutes), repeat the edit.
  * A block stops the running session adopting the change. It does not revert the file,
    and Claude Code shows no message for it, so the ledger and the alert are where it shows.

Fails OPEN everywhere: bad stdin, unreadable settings, a corrupt baseline, any exception
-> exit 0 with empty stdout. There is exactly one call site that can print a block.
Kill switch: CLAUDE_GATE_CONFIG=off or <state>/off/config (log-only, baseline frozen).

    config_tamper.py --baseline [~/.claude/settings.json]   capture the baseline now
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402

NAME = "config"
WINDOW_S = 900


def ledger_path():
    return os.path.join(_gate.state_dir(), "config-changes.jsonl")


def release_path():
    return os.path.join(_gate.state_dir(), "config-ALLOW")


def baseline_path(file_path):
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.abspath(os.path.expanduser(file_path))).strip("_")
    return os.path.join(_gate.state_dir(), "config-baseline", slug + ".json")


def now():
    v = os.environ.get("CLAUDE_GATE_CONFIG_NOW")
    try:
        return float(v) if v else time.time()
    except ValueError:
        return time.time()


def normalize(obj):
    """settings dict -> [(event, matcher, type, command, timeout)]. Order-independent, and a
    shape it does not recognise contributes nothing rather than raising."""
    out = []
    hooks = obj.get("hooks") if isinstance(obj, dict) else None
    if not isinstance(hooks, dict):
        return out
    for event, groups in hooks.items():
        for g in groups if isinstance(groups, list) else []:
            if not isinstance(g, dict):
                continue
            matcher = "*" if g.get("matcher") in (None, "") else str(g.get("matcher"))
            for h in g.get("hooks") if isinstance(g.get("hooks"), list) else []:
                if not isinstance(h, dict):
                    continue
                cmd = h.get("command") if h.get("command") is not None else h.get("url")
                if h.get("args"):
                    cmd = "%s %s" % (cmd, json.dumps(h.get("args")))
                to = h.get("timeout")
                to = int(to) if isinstance(to, (int, float)) and not isinstance(to, bool) else None
                out.append((str(event), matcher, str(h.get("type") or ""), str(cmd or ""), to))
    return out


def _ids(tuples):
    """identity (event, matcher, type, command) -> sorted timeouts. Timeout is the one field
    allowed to move without being a tamper signal."""
    d = {}
    for ev, m, ty, cmd, to in tuples:
        d.setdefault((ev, m, ty, cmd), []).append(to)
    for k in d:
        d[k].sort(key=lambda x: (x is None, x))
    return d


def diff(base, cur):
    """-> (removed, added, altered, timeout_changed). ALTERED beats removed+added."""
    b, c = _ids(base), _ids(cur)
    missing = sorted(k for k in b if k not in c)
    unclaimed = sorted(k for k in c if k not in b)
    removed, altered = [], []
    for ev, m, ty, cmd in missing:
        mate = next((f for f in unclaimed if f[0] == ev and f[1] == m), None)
        if mate:
            unclaimed.remove(mate)
            altered.append({"event": ev, "matcher": m, "was": cmd, "now": mate[3]})
        else:
            removed.append({"event": ev, "matcher": m, "command": cmd})
    added = [{"event": k[0], "matcher": k[1], "command": k[3]} for k in unclaimed]
    tchg = [{"event": k[0], "matcher": k[1], "was": b[k], "now": c[k]}
            for k in sorted(set(b) & set(c)) if b[k] != c[k]]
    return removed, added, altered, tchg


def read_settings(path):
    """-> (tuples, err). A read failure is the instrument failing, never a verdict."""
    try:
        with open(path, encoding="utf-8") as f:
            return normalize(json.loads(f.read())), ""
    except FileNotFoundError:
        return [], "missing"
    except Exception:
        return [], "unparseable"


def load_baseline(path):
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f).get("tuples")
        if not isinstance(rows, list):
            return None
        return [(str(r[0]), str(r[1]), str(r[2]), str(r[3]), r[4] if isinstance(r[4], int) else None)
                for r in rows if isinstance(r, list) and len(r) == 5]
    except Exception:
        return None          # a corrupt baseline re-bootstraps rather than blocking


def save_baseline(path, tuples, file_path):
    _gate.write_private(path, json.dumps({"captured": _gate.now_iso(), "file_path": file_path,
                                          "tuples": [list(t) for t in tuples]}, indent=1) + "\n")


def decide(event):
    """-> (stdout_text, ledger_row). Raises only into handle()'s catch-all."""
    if event.get("hook_event_name") not in ("ConfigChange", None):
        return "", None
    source, fp = str(event.get("source") or ""), str(event.get("file_path") or "")
    rec = {"ts": _gate.now_iso(), "source": source, "file_path": fp,
           "session": str(event.get("session_id") or "")[:8]}
    if source != "user_settings":
        rec["verdict"] = "other-source"      # project/local files are logged, never blocked
        return "", rec
    cur, err = read_settings(fp)
    if err:
        rec["verdict"] = err                  # fail OPEN
        return "", rec
    bpath = baseline_path(fp)
    base, off = load_baseline(bpath), _gate.gate_off(NAME)
    if base is None:
        rec["verdict"] = "off" if off else "bootstrap"
        if not off:
            save_baseline(bpath, cur, fp)
        return "", rec
    removed, added, altered, tchg = diff(base, cur)
    rec.update(removed=removed, added=added, altered=altered, timeout_changed=tchg)
    if off:
        # log-only, and the baseline does NOT move: blessing a removal while the gate is off
        # would hide it from the run that turns the gate back on
        rec["verdict"], rec["would_block"] = "off", bool(removed or altered)
        return "", rec
    if removed or altered:
        rel, fresh = release_path(), False
        try:
            fresh = os.path.exists(rel) and (now() - os.stat(rel).st_mtime) <= WINDOW_S
        except Exception:
            fresh = False
        n = len(removed) + len(altered)
        if fresh:
            try:
                os.unlink(rel)                # single use: consumed BEFORE the allow lands
            except OSError:
                pass
            save_baseline(bpath, cur, fp)
            rec["verdict"] = "removed-with-release"
            rec["alert"] = _gate.alert("[config] %d hook handler(s) removed with a release in %s" % (n, fp),
                                       fp, "config-rate.json", WINDOW_S, allow_stdout=False)
            return "", rec
        rec["verdict"] = "blocked"
        rec["alert"] = _gate.alert("[config] BLOCKED: %d hook handler(s) removed or altered in %s" % (n, fp),
                                   fp, "config-rate.json", WINDOW_S, allow_stdout=False)
        return json.dumps({"decision": "block", "reason": (
            "config tamper gate: %d hook handler(s) removed or altered in %s. For a deliberate "
            "removal: touch %s (single use, 15 min) and repeat the edit." % (n, fp, release_path()))}), rec
    rec["verdict"] = "added" if added else ("timeout-changed" if tchg else "other")
    if added or tchg:
        save_baseline(bpath, cur, fp)
    return "", rec


def handle(event):
    """The fail-open shell: any exception is an ALLOW with an exception row."""
    try:
        out, rec = decide(event)
    except Exception as e:
        out, rec = "", {"ts": _gate.now_iso(), "verdict": "exception:%s" % type(e).__name__}
    if rec is not None:
        _gate.append_private(ledger_path(), json.dumps(rec, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--baseline":
        fp = os.path.expanduser(sys.argv[2] if len(sys.argv) > 2 else "~/.claude/settings.json")
        tuples, err = read_settings(fp)
        if err:
            print("could not read %s: %s" % (fp, err))
            sys.exit(2)
        save_baseline(baseline_path(fp), tuples, fp)
        print("baseline captured: %d hook handler(s) in %s" % (len(tuples), fp))
        sys.exit(0)
    try:
        ev = _gate.read_event()
        out = handle(ev) if ev is not None else ""
        if out:
            sys.stdout.write(out)
    except Exception:
        pass
    sys.exit(0)
