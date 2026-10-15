#!/usr/bin/env python3
"""Number gate: a dollar figure in a reply must be grounded somewhere.

A money figure passes when the same amount appears in something the user typed or a
tool returned in this session, or in one of your fact files. The assistant's own earlier
lines never count, or a draft would ground itself. Anything else is an invented number,
and the reply bounces once with the figure quoted.

Fact files (read as plain text, *.md under any directory given):
    ./CLAUDE.md and ~/.claude/CLAUDE.md, plus CLAUDE_GATE_NUMBER_FACTS=path1:path2
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402

NAME = "number"
FACTS_ENV = "CLAUDE_GATE_NUMBER_FACTS"
FACT_BYTES_CAP = 4_000_000

MONEY_RX = re.compile(
    r"(?<![\w/])\$\s?\d[\d,]*(?:\.\d+)?\s?(?:[kKmM]\b)?"                         # $70k $650 $1,500 $0.18
    r"|(?<![\w$.\-])\b\d{2,3}(?:\.\d)?\s?[kK]\b(?!\s?(?:B|iB|Hz|m\b|g\b|token))"   # 70k 100K
)


def _digits(tok):
    """'$70k' -> '70', '$1,500.00' -> '1500.00', '$0.18' -> '0.18': the grounding key."""
    return tok.replace("$", "").replace(",", "").strip().lower().rstrip("km").strip()


def money_keys(tok):
    """MONEY-SHAPED spellings only. Bare digits ground nothing: a small number sits inside
    every tool result (ids, sizes, counts), so a source has to carry the amount AS an
    amount: '$70k' | '70k' | '$70,000' | '70,000' | '70000'."""
    raw = tok.strip()
    key = _digits(raw)
    out = set()
    if raw.lower().rstrip().endswith("k") and key.replace(".", "").isdigit():
        n = float(key)
        out |= {"$%sk" % key, "%sk" % key, "$%sK" % key, "%sK" % key}
        if n == int(n):
            whole = int(n) * 1000
            out |= {"${:,}".format(whole), "{:,}".format(whole), "$%d" % whole, "%d" % whole}
    else:
        whole = key.split(".")[0]
        out |= {"$" + key, key + "$", key + " dollars", key + " USD"}
        if whole.isdigit() and len(whole) > 3:
            out |= {"${:,}".format(int(whole)), "{:,}".format(int(whole))}
        if "." in key and key.replace(".", "").isdigit():
            out.add(key)
    return {k for k in out if any(ch.isdigit() for ch in k)}


def _match(line, key):
    """Boundary-aware: '83k' must not match inside '183k', '2,340' not inside '12,340'."""
    try:
        return re.search(r"(?<![\w$,.\-])" + re.escape(key) + r"(?![\w,]|\.\d)", line, re.I) is not None
    except Exception:
        return key.lower() in line.lower()


def in_transcript(path, key, exclude_msg):
    """True if the amount appears in any USER text or TOOL RESULT of this session."""
    try:
        if not path or not os.path.isfile(path):
            return False
        with open(path, errors="replace") as f:
            for line in f:
                if not _match(line, key):
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("type") == "assistant":
                    continue
                if exclude_msg and exclude_msg[:80] in line:
                    continue
                return True
    except Exception:
        return False
    return False


def fact_paths(cwd):
    paths = [os.path.join(cwd or ".", "CLAUDE.md"),
             os.path.join(os.path.expanduser("~"), ".claude", "CLAUDE.md")]
    paths += [os.path.expanduser(p) for p in os.environ.get(FACTS_ENV, "").split(":") if p.strip()]
    return paths


def _fact_files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    if fn.endswith(".md"):
                        yield os.path.join(root, fn)


def on_file(cwd, key):
    try:
        budget = FACT_BYTES_CAP
        for fp in _fact_files(fact_paths(cwd)):
            with open(fp, encoding="utf-8", errors="replace") as f:
                text = f.read(budget)
            budget -= len(text)
            if any(_match(ln, key) for ln in text.splitlines()):
                return True
            if budget <= 0:
                break
        return False
    except Exception:
        return True          # cannot read the facts => never block on them


def check(event, turn=None):
    """Stop gate. -> block reason, or None. Never raises."""
    try:
        if event.get("stop_hook_active") or _gate.gate_off(NAME):
            return None
        msg = _gate.reply_of(event)
        if len(msg.strip()) < 20:
            return None
        tp = event.get("transcript_path")
        if not tp or not os.path.isfile(tp):
            return None          # cannot see what the user typed: fail open
        body = re.sub(r"```.*?```", " ", msg, flags=re.S)
        body = re.sub(r"`[^`\n]*`", " ", body)
        hits = []
        for m in MONEY_RX.finditer(body):
            tok = m.group(0)
            keys = money_keys(tok)
            if not keys:
                continue
            if any(in_transcript(event.get("transcript_path"), k, msg) or on_file(event.get("cwd"), k)
                   for k in keys):
                continue
            hits.append(tok.strip())
        if not hits:
            return None
        _gate.log_bounce(NAME, event, hits=hits[:8])
        return ("[number gate] This reply carries a dollar figure that is grounded nowhere: %s. "
                "It was not typed by the user or returned by a tool this session, and it is not in "
                "a fact file. Either it came from a source you read this turn (say which), or drop "
                "the number and say it is not on file. Do not narrate this check in the reply."
                % ", ".join(sorted(set(hits))[:5]))
    except Exception:
        return None


if __name__ == "__main__":
    _gate.run_stop_gate(check)
