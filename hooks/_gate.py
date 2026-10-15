#!/usr/bin/env python3
"""Shared helper for every gate: kill switches, the bounce log, the turn reader,
the one-call judge and the notifier.

Every function in here fails OPEN. On any error it returns the neutral value
(not off, no rows, no verdict) instead of raising, because a gate that crashes
must never hold a reply hostage.

    python3 _gate.py stats        tally the bounce log by gate
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

STATE_ENV = "CLAUDE_GATES_STATE"
JUDGE_CHILD_ENV = "CLAUDE_GATES_JUDGE_CHILD"   # set inside the judge's own session
JUDGE_FAKE_ENV = "CLAUDE_GATES_JUDGE_FAKE"     # tests inject a verdict, no model call
JUDGE_MODEL_ENV = "CLAUDE_GATES_JUDGE_MODEL"
JUDGE_TIMEOUT = 35          # must finish inside the Stop hook's 45 s timeout in settings
MIN_REPLY = 40              # a one-liner is never worth a model call
USER_TEXT_CAP = 4000
TURN_SCAN_CAP = 16_000_000  # bytes read back from the end of the transcript
PRIVATE = 0o600


# ------------------------------------------------------------------ state + switches

def state_dir():
    """Where logs, ledgers and OFF files live. Outside the repo, so `git pull` stays clean."""
    return os.environ.get(STATE_ENV) or os.path.join(os.path.expanduser("~"), ".claude", "gates-state")


def gate_off(name):
    """Kill switches, read on EVERY call so flipping one needs no restart.

    env:  CLAUDE_GATES=off (all gates) or CLAUDE_GATE_<NAME>=off (one gate)
    file: <state>/off/ALL or <state>/off/<name>
    """
    try:
        if os.environ.get("CLAUDE_GATES", "").strip().lower() == "off":
            return True
        var = "CLAUDE_GATE_" + name.upper().replace("-", "_")
        if os.environ.get(var, "").strip().lower() == "off":
            return True
        off = os.path.join(state_dir(), "off")
        return os.path.exists(os.path.join(off, "ALL")) or os.path.exists(os.path.join(off, name))
    except Exception:
        return False


def append_private(path, text):
    """Append with 0600 from the first byte: the logs quote replies, so they are private by
    construction rather than chmod-ed after the fact. Never raises; returns success."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception:
        return False


def write_private(path, text):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp%d" % os.getpid()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# sk- first so a short key is named by its own pattern; the last alternative catches
# opaque tokens (JWT segments, base64 blobs) that carry no prefix at all.
SECRET_RX = re.compile(r"sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+|[A-Za-z0-9+/_-]{40,}")


def redact(s, limit=None):
    """Redact FIRST, truncate after. Cutting first can split a token into a fragment too
    short to match, and the redaction then reports clean on a leaked secret."""
    try:
        t = SECRET_RX.sub("<redacted>", "" if s is None else str(s))
    except Exception:
        return ""
    return t[:limit] if limit else t


def log_bounce(gate, event, **fields):
    """One row per bounce in <state>/bounces.jsonl. A week of this file answers
    "is it firing too much" with a count instead of a feeling."""
    row = {"ts": now_iso(), "gate": gate, "session": str((event or {}).get("session_id") or "")}
    row.update(fields)
    append_private(os.path.join(state_dir(), "bounces.jsonl"), json.dumps(row, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ hook I/O

def read_event():
    """The hook's stdin as a dict, or None (which every caller treats as allow)."""
    try:
        d = json.loads(sys.stdin.read() or "")
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def emit(obj):
    """Print one JSON decision and end the hook. Exit 0 always: the decision travels in the
    JSON, so no error path can imitate a block."""
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()
    sys.exit(0)


def in_judge_session():
    """True inside the judge's own `claude -p` session. Every hook exits 0 on sight of it,
    the second lock against a Stop hook judging its own judge."""
    return bool(os.environ.get(JUDGE_CHILD_ENV))


def reply_of(event):
    return str((event or {}).get("last_assistant_message") or "")


def strip_code(text):
    """Fenced code, inline code and URLs are never judged: a version string inside a
    command is being SHOWN, not asserted."""
    t = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)
    t = re.sub(r"`[^`\n]*`", " ", t)
    return re.sub(r"<https?://[^>\s]+>|https?://\S+", " ", t)


# ------------------------------------------------------------------ the turn reader

_NOT_HUMAN_FLAGS = ("isMeta", "isCompactSummary", "isVisibleInTranscriptOnly")


def is_human_prompt(o):
    """A transcript row the user typed: type user, none of the CLI's synthetic flags, and
    text content with no tool_result. Tool results and resize notes are user rows too, and
    anchoring "this turn" on one of those is how a turn reader goes wrong."""
    try:
        if not isinstance(o, dict) or o.get("type") != "user":
            return False
        if any(o.get(k) for k in _NOT_HUMAN_FLAGS):
            return False
        c = (o.get("message") or {}).get("content")
        if isinstance(c, str):
            return bool(c.strip())
        if isinstance(c, list):
            kinds = set(b.get("type") for b in c if isinstance(b, dict))
            return "tool_result" not in kinds and "text" in kinds
        return False
    except Exception:
        return False


def turn_rows(transcript_path, cap=TURN_SCAN_CAP):
    """(prompt_row, rows_after_it) for THIS turn, or None when the transcript cannot be read
    at all. Parses backwards from the end and stops at the last human prompt, so a long
    session costs only its current turn."""
    try:
        if not transcript_path or not os.path.isfile(transcript_path):
            return None
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            if size > cap:
                f.seek(size - cap)
                f.readline()            # drop the partial line the seek landed inside
            data = f.read()
        rows, parsed, prompt = [], 0, None
        for line in reversed(data.split(b"\n")):
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            parsed += 1
            if is_human_prompt(o):
                prompt = o
                break
            rows.append(o)
        if not parsed:
            return None
        rows.reverse()
        return prompt, rows
    except Exception:
        return None


def prompt_text(row):
    try:
        c = (row.get("message") or {}).get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "\n".join(str(b.get("text") or "") for b in c
                             if isinstance(b, dict) and b.get("type") == "text")
    except Exception:
        pass
    return ""


class Turn(object):
    """This turn, read once from the transcript and shared by every gate in the chain.

    tool_uses() returns None for "could not read the transcript" and [] for "read it, no
    tools ran". The gates depend on that difference: None fails OPEN, [] is evidence.
    Collapsing the two would turn every unreadable transcript into a block.

    Residual, from the hooks contract: the transcript is written asynchronously and can lag
    the live conversation, so a tool call made as the turn's very last step may not be on
    disk yet. The ceiling on that false positive is one bounce, because stop_hook_active is
    true on the next Stop and every gate stands down.
    """

    def __init__(self, event):
        self.event = event or {}
        self._tr = None
        self._loaded = False

    def _load(self):
        if not self._loaded:
            self._tr = turn_rows(self.event.get("transcript_path"))
            self._loaded = True
        return self._tr

    def tool_uses(self):
        tr = self._load()
        if tr is None:
            return None
        out = []
        try:
            for o in tr[1]:
                if not isinstance(o, dict) or o.get("type") != "assistant":
                    continue
                c = (o.get("message") or {}).get("content")
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            inp = b.get("input")
                            out.append((str(b.get("name") or ""), inp if isinstance(inp, dict) else {}))
        except Exception:
            return None
        return out

    def user_text(self):
        tr = self._load()
        if not tr or not tr[0]:
            return ""
        return prompt_text(tr[0])[:USER_TEXT_CAP]


# ------------------------------------------------------------------ the judge

JUDGE_PROMPT = """You are a strict classifier. You will be given the text of an assistant's reply.

Answer ONLY with a JSON object, no prose, no markdown fence:
%s

%s

Every quote must be copied VERBATIM from the reply: the exact sentence carrying the claim,
or an empty string.

REPLY TEXT:
<<<
%s
>>>"""

JUDGE_CALLS = [0]   # tests assert "exactly one call"; fakes count too


def parse_first_json(text):
    """First '{' then raw_decode. Steps over a markdown fence for free, which small models
    add even when told not to."""
    try:
        t = str(text or "")
        i = t.find("{")
        if i < 0:
            return None
        obj, _ = json.JSONDecoder().raw_decode(t[i:])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def judge_prompt(questions, reply):
    """questions: [(flag_key, quote_key, text)]. Pure, so a test can read the exact prompt."""
    keys = ", ".join('"%s": true|false, "%s": "<verbatim sentence or empty string>"' % (f, q)
                     for f, q, _ in questions)
    return JUDGE_PROMPT % ("{" + keys + "}", "\n\n".join(t for _, _, t in questions),
                           str(reply)[:6000])


def judge(questions, reply, timeout=JUDGE_TIMEOUT):
    """ONE `claude -p` call carrying every question that fired. Returns a dict, or None,
    and None means ALLOW: timeout, non-zero exit, unparseable output, no `claude` on PATH,
    any exception at all. A guard that blocks on its own malfunction would hold every
    reply hostage to a model call.

    Recursion: the judge is itself a Claude Code session whose Stop hook would be this
    chain. Two locks stop the regress: `--settings '{"disableAllHooks": true}'` switches
    hooks off for that one run, and CLAUDE_GATES_JUDGE_CHILD makes every gate exit 0.
    """
    JUDGE_CALLS[0] += 1
    fake = os.environ.get(JUDGE_FAKE_ENV)
    if fake is not None:
        return parse_first_json(fake)
    if os.environ.get("CLAUDE_GATES_JUDGE", "").strip().lower() == "off":
        return None
    try:
        env = dict(os.environ)
        env[JUDGE_CHILD_ENV] = "1"
        env["MAX_THINKING_TOKENS"] = "0"    # the judge answers, it does not ponder
        model = os.environ.get(JUDGE_MODEL_ENV) or "haiku"
        r = subprocess.run(
            ["claude", "-p", "--model", model, "--output-format", "json",
             "--settings", '{"disableAllHooks": true}', judge_prompt(questions, reply)],
            capture_output=True, text=True, timeout=timeout, cwd=tempfile.gettempdir(),
            stdin=subprocess.DEVNULL, env=env)
        if r.returncode != 0:
            return None
        top = parse_first_json(r.stdout)
        if not top or top.get("is_error"):
            return None
        return parse_first_json(top.get("result"))
    except Exception:
        return None


# ------------------------------------------------------------------ the notifier

WEBHOOK_ENV = "CLAUDE_GATES_WEBHOOK_URL"
NOTIFY_FILE_ENV = "CLAUDE_GATES_NOTIFY_FILE"


def notify_mode(allow_stdout=True):
    if os.environ.get(WEBHOOK_ENV):
        return "webhook"
    if os.environ.get(NOTIFY_FILE_ENV):
        return "file"
    return "stdout" if allow_stdout else "none"


def notify_sync(text, allow_stdout=True):
    """Deliver an alert now. -> (ok, disposition). Never raises.

    webhook: POST {"text": ..., "content": ...}, the two keys common chat webhooks read
    file:    append one line to CLAUDE_GATES_NOTIFY_FILE
    stdout:  print it (ConfigChange passes allow_stdout=False: its stdout is the decision)
    """
    mode = notify_mode(allow_stdout)
    try:
        if mode == "webhook":
            import urllib.request
            body = json.dumps({"text": text, "content": text}).encode("utf-8")
            req = urllib.request.Request(os.environ[WEBHOOK_ENV], data=body, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "claude-code-gates"})
            with urllib.request.urlopen(req, timeout=20) as r:
                ok = 200 <= r.status < 300
            return ok, "webhook:%s" % ("ok" if ok else "http-error")
        if mode == "file":
            line = "%s %s\n" % (now_iso(), " | ".join(text.splitlines()))
            ok = append_private(os.path.expanduser(os.environ[NOTIFY_FILE_ENV]), line)
            return ok, "file:%s" % ("ok" if ok else "write-failed")
        if mode == "stdout":
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
            return True, "stdout"
        return False, "none"
    except Exception as e:
        return False, "%s:failed:%s" % (mode, type(e).__name__)


def spawn_detached(argv):
    """Start argv in its OWN session so it outlives the parent. A hook that fires as a
    session ends (StopFailure) is the worst case for async work: teardown kills it."""
    try:
        logf = os.path.join(state_dir(), "notify-child.log")
        os.makedirs(state_dir(), exist_ok=True)
        fd = os.open(logf, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE)
        try:
            subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=fd, stderr=fd, close_fds=True)
        finally:
            os.close(fd)
        return True
    except Exception:
        return False


class _Lock(object):
    """flock on <state>/.rate.lock where fcntl exists; a no-op elsewhere."""

    def __enter__(self):
        self.fd = None
        try:
            import fcntl
            os.makedirs(state_dir(), exist_ok=True)
            self.fd = os.open(os.path.join(state_dir(), ".rate.lock"), os.O_RDWR | os.O_CREAT, PRIVATE)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        return False


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def rate_claim(key, window_s, state_name, ts=None):
    """(may_alert, suppressed_count). Does NOT stamp the window. Fails OPEN: unreadable
    state or any exception alerts rather than suppresses, and every suppressed alert is
    counted so the next delivered one says how many it ate."""
    ts = time.time() if ts is None else ts
    try:
        with _Lock():
            path = os.path.join(state_dir(), state_name)
            st = _read_json(path)
            rec = st.get(key) if isinstance(st.get(key), dict) else {}
            last, supp = rec.get("last"), int(rec.get("suppressed") or 0)
            try:
                if last is None or ts - float(last) >= window_s:
                    return True, supp
            except (TypeError, ValueError):
                return True, supp
            rec["suppressed"] = supp + 1
            st[key] = rec
            write_private(path, json.dumps(st))
            return False, supp + 1
    except Exception:
        return True, 0


def rate_stamp(key, state_name, ts=None):
    """Close the window. Called ONLY after a delivery succeeded, so a failed post leaves
    the slot open for the next failure instead of buying a window of silence."""
    try:
        with _Lock():
            path = os.path.join(state_dir(), state_name)
            st = _read_json(path)
            st[key] = {"last": time.time() if ts is None else ts, "suppressed": 0}
            return write_private(path, json.dumps(st))
    except Exception:
        return False


def alert(text, key, state_name, window_s=900, allow_stdout=True, ts=None):
    """Rate-limited alert through the configured notifier. -> disposition. Never raises.
    A webhook post goes to a detached child so it survives the session's teardown."""
    try:
        may, supp = rate_claim(key, window_s, state_name, ts)
        if not may:
            return "suppressed:%d" % supp
        if supp:
            text += "\nsuppressed %d since last" % supp
        mode = notify_mode(allow_stdout)
        if mode == "none":
            return "none"
        if mode == "webhook":
            pf = os.path.join(state_dir(), ".post-%d-%d.json" % (os.getpid(), int(time.time() * 1000)))
            if not write_private(pf, json.dumps({"text": text, "key": key, "state_name": state_name})):
                return "dispatch-fail"
            ok = spawn_detached([sys.executable, os.path.abspath(__file__), "--post-child", pf])
            return "webhook:dispatched" if ok else "dispatch-fail"
        ok, disp = notify_sync(text, allow_stdout)
        if ok:
            rate_stamp(key, state_name, ts)
        return disp
    except Exception as e:
        return "alert-failed:%s" % type(e).__name__


def post_child(payload_file):
    """The detached half of a webhook alert: post, stamp on success, leave a receipt."""
    try:
        p = _read_json(payload_file)
        ok, disp = notify_sync(str(p.get("text") or ""), allow_stdout=False)
        if ok:
            rate_stamp(str(p.get("key") or ""), str(p.get("state_name") or "rate.json"))
        append_private(os.path.join(state_dir(), "notify-receipts.jsonl"),
                       json.dumps({"ts": now_iso(), "key": p.get("key"), "result": disp}) + "\n")
    except Exception:
        pass
    finally:
        try:
            os.unlink(payload_file)
        except OSError:
            pass
    return 0


# ------------------------------------------------------------------ entry points

def run_stop_gate(check):
    """Run ONE Stop gate as its own hook (stop_chain.py runs them all together)."""
    try:
        if in_judge_session():
            sys.exit(0)
        ev = read_event()
        if ev is not None:
            reason = check(ev, Turn(ev))
            if reason:
                emit({"decision": "block", "reason": reason})
    except SystemExit:
        raise
    except Exception:
        pass
    sys.exit(0)


def stats(path=None):
    path = path or os.path.join(state_dir(), "bounces.jsonl")
    counts, first, last, n = {}, None, None, 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                n += 1
                g = str(r.get("gate"))
                counts[g] = counts.get(g, 0) + 1
                ts = r.get("ts")
                if ts:
                    first = ts if first is None or ts < first else first
                    last = ts if last is None or ts > last else last
    except FileNotFoundError:
        print("no bounces logged yet (%s)" % path)
        return 0
    print("%d bounces, %s to %s" % (n, first, last))
    for g, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("%6d  %s" % (c, g))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--post-child":
        sys.exit(post_child(sys.argv[2]))
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        sys.exit(stats(sys.argv[2] if len(sys.argv) > 2 else None))
    print(__doc__.strip())
