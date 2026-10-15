#!/usr/bin/env python3
"""Slop gate: refuses a reply that scores as AI-patterned, quoting the spots.

A script that REFUSES, instead of a style rule that asks. Design rules:
  * STRUCTURE is the gate, vocabulary is a lint layer. Pivots, cadence and machine-even
    sentence lengths SCORE; tier words ("delve", "tapestry") only REPORT and can never
    fail a reply on their own.
  * The em dash and a typed " -- " are HARD blocks (count >= 1). Set
    CLAUDE_GATE_SLOP_DASH=allow to make them report-only.
  * Code, inline code and URLs are never judged.
  * A reply shaped as a list is exempt from the uniformity rule: even rows are what a
    list IS, and rewriting a table to vary its rows is a worse reply.

As a Stop hook: one bounce per turn, findings quoted so the rewrite is targeted.
As a CLI:       slop_gate.py --score FILE|- [--profile chat|external] [--json]
                slop_gate.py --learn 'REGEX' --note 'why' [--weight N]
Exit codes in CLI mode: 0 pass, 1 FAIL, 2 could not run.
"""
import argparse
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402

NAME = "slop"
THRESHOLD = {"chat": 3.0, "external": 2.0}
REASON_CAP = 1100
SKIP_EXACT = {"NO_REPLY"}

HARD = [
    ("em-dash", re.compile("\u2014"), "the em dash is the single most-cited tell of machine prose"),
    ("double-hyphen-dash", re.compile(r"(?<=\S) -- (?=\S)"), "a typed em dash is still an em dash"),
]
DASH_RULES = {"em-dash", "double-hyphen-dash"}

STRUCT = [
    (2.0, "not-x-but-y pivot",
     re.compile(r"\b(?:(?:it|this|that|there|he|she|which)\s*(?:'s|'?s)?\s+(?:is\s+)?not|(?:it|this|that|there|[A-Z][\w$]*(?:\s+[\w$%]+){0,3})\s+isn'?t|(?:these|those|they|we|you)\s+(?:are\s+not|aren'?t)|I'?m\s+not|wasn'?t|weren'?t)\s+(?:just\s+|only\s+|about\s+|simply\s+|merely\s+)?[^.,;!?\n]{2,60}[,;.]?\s*(?:it'?s|they'?re|but|this is|that'?s|he'?s|she'?s|we'?re|you'?re)\b", re.I)),
    (2.0, "not-only-but-also",
     re.compile(r"\bnot\s+only\b[^.\n]{2,80}\bbut\s+(?:also\s+)?", re.I)),
    (3.0, "stacked negation cadence (No X. No Y. No Z.)",
     re.compile(r"\bNo\s+[^.,;\n]{1,30}[.,;]\s+No\s+[^.,;\n]{1,30}[.,;]\s+No\s+[^.,;\n]{1,30}\b")),
    (3.0, "throat-clearing opener",
     re.compile(r"(?:^|\n|[.!?]\s+)(?:Here'?s the thing|Let'?s be honest|Real talk|The thing is|Honestly\?|Let'?s be real|Plot twist|Spoiler alert)\b[:,]?", re.I)),
    (2.0, "announcing the next point",
     re.compile(r"\b(?:let'?s (?:dive|explore|break (?:this|it) down|unpack|take a (?:closer )?look)|here'?s what you need to know|without further ado|buckle up|read on)\b", re.I)),
    (1.0, "rhetorical self-answer (The result? X.)",
     re.compile(r"(?:^|[.!?]\s+)(?:The|My|Your|Our|His|Her|Their)?\s?\w+(?:\s\w+){0,3}\?\s+(?:A|An|The|Not|Nothing|Everything|Exactly|Simple|Yes|No)\b[^.?!\n]{0,60}[.!]")),
    (1.0, "decorative emoji", re.compile("[\U0001F680✨\U0001F4A1\U0001F525\U0001F3AF\U0001F4AF\U0001F31F\U0001F64C\U0001F44F]")),
    (3.0, "chatbot residue",
     re.compile(r"(?:(?:^|\n)\s*(?:Great question|Certainly!|Absolutely!|Of course!|Happy to help)\b|\b(?:I hope this helps|As an AI(?: language model)?)\b)", re.I)),
    (1.5, "sweeping closer",
     re.compile(r"\b(?:In conclusion|In summary|To sum up|At the end of the day|Ultimately, it'?s (?:about|all))\b", re.I)),
]

# Report-only: printed so the writer sees them, weight 0, never moves the verdict.
TIER = re.compile(r"\b(?:delve|delves|delving|tapestry|testament to|(?:rich|vibrant|complex) landscape|leverag(?:e|es|ing)|game[- ]changer|seamless(?:ly)?|robust|elevate|unlock(?:s|ing)? (?:the|your|new)|navigat(?:e|ing) the|in today'?s (?:fast-paced|ever-changing|digital)|a world where|it'?s worth noting|it is important to note|crucial|pivotal|transformative|synerg\w*|holistic|empower(?:s|ing)?|foster(?:s|ing)?|cutting-edge|realm|embark)\b", re.I)

DECOR_BOLD_LINES = 5     # chat profile: this many lines opening with **Label** = header-per-paragraph
TRICOLON = re.compile(r"\b\w+,\s+\w+,\s+and\s+\w+\b")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
CV_RULE = "uniform sentence length"
LIST_LINE_RX = re.compile("^\\s*(?:[-•·→*]|\\d)")
LIST_FLOOR = 0.60


def learned_path():
    return os.path.join(_gate.state_dir(), "slop-learned.jsonl")


def load_learned():
    pats = []
    try:
        with open(learned_path(), encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    d = json.loads(ln)
                    pats.append((float(d.get("weight", 1.0)),
                                 "learned: " + str(d.get("note") or d["regex"])[:60],
                                 re.compile(d["regex"], re.I)))
    except FileNotFoundError:
        pass
    except Exception as exc:
        print("slop gate: learned patterns unreadable, ignored: %s" % exc, file=sys.stderr)
    return pats


def _snippet(text, m, width=90):
    s, e = max(0, m.start() - 30), min(len(text), m.end() + 40)
    return " ".join(text[s:e].split())[:width]


def list_shaped(msg, floor=LIST_FLOOR):
    lines = [l for l in str(msg or "").splitlines() if l.strip()]
    if not lines:
        return False
    return sum(1 for l in lines if LIST_LINE_RX.match(l)) >= floor * len(lines)


def analyse(text, profile="chat"):
    raw = str(text or "")
    text = _gate.strip_code(raw)
    dash_ok = os.environ.get("CLAUDE_GATE_SLOP_DASH", "").strip().lower() == "allow"
    findings, hard, report, score = [], [], [], 0.0
    if raw.strip() in SKIP_EXACT or len(raw.strip()) < 20:
        return {"verdict": "PASS", "score": 0.0, "threshold": THRESHOLD.get(profile, 3.0),
                "hard": [], "findings": [], "report": [], "skipped": True}
    for name, rx, why in HARD:
        for m in rx.finditer(text):
            hit = {"rule": name, "why": why, "at": _snippet(text, m)}
            (report if (dash_ok and name in DASH_RULES) else hard).append(hit)
    for w, name, rx in STRUCT + load_learned():
        for m in rx.finditer(text):
            score += w
            findings.append({"rule": name, "weight": w, "at": _snippet(text, m)})
    # tricolon DENSITY, not presence: one is rhetoric, a stack is a tell
    words = max(1, len(text.split()))
    tri = TRICOLON.findall(text)
    if len(tri) >= 2 and len(tri) / words * 100 >= 1.5:
        score += 1.0 * (len(tri) - 1)
        findings.append({"rule": "tricolon density", "weight": 1.0 * (len(tri) - 1),
                         "at": "%d 'X, Y, and Z' in %d words" % (len(tri), words)})
    if profile == "chat":
        bold_open = sum(1 for ln in raw.splitlines()
                        if re.match(r"\s*(?:[-*]\s+)?\*\*[^*\n]{2,40}\*\*[.:]?\s", ln))
        if bold_open >= DECOR_BOLD_LINES:
            score += 1.0
            findings.append({"rule": "header-per-paragraph", "weight": 1.0,
                             "at": "%d lines open with a **bold label**" % bold_open})
    # sentence lengths too uniform reads machine-even; needs 8+ sentences and prose, not a list
    sents = [s for s in SENT_SPLIT.split(" ".join(text.split())) if len(s.split()) >= 3]
    if len(sents) >= 8 and not list_shaped(raw):
        lens = [len(s.split()) for s in sents]
        cv = statistics.pstdev(lens) / (statistics.mean(lens) or 1)
        if cv < 0.35:
            score += 3.0
            findings.append({"rule": CV_RULE, "weight": 3.0,
                             "at": "%d sentences, length CV %.2f (< 0.35)" % (len(sents), cv)})
    report += [{"rule": "tier word (report-only)", "at": _snippet(text, m)} for m in TIER.finditer(text)]
    thr = THRESHOLD.get(profile, 3.0)
    verdict = "FAIL" if (hard or score >= thr) else "PASS"
    return {"verdict": verdict, "score": round(score, 1), "threshold": thr, "profile": profile,
            "hard": hard, "findings": findings, "report": report, "skipped": False}


def render(r):
    lines = ["slop gate: %s  score %s/%s" % (r["verdict"], r["score"], r.get("threshold", "-"))]
    for h in r["hard"]:
        lines.append("  HARD  %s: ...%s...   (%s)" % (h["rule"], h["at"], h["why"]))
    for f in r["findings"]:
        lines.append("  %+.1f  %s: ...%s..." % (f["weight"], f["rule"], f["at"]))
    for t in r["report"][:8]:
        lines.append("  report-only  %s: ...%s..." % (t["rule"], t["at"]))
    return "\n".join(lines)


def check(event, turn=None):
    """Stop gate. -> block reason, or None. Never raises."""
    try:
        if event.get("stop_hook_active") or _gate.gate_off(NAME):
            return None
        msg = _gate.reply_of(event)
        if len(msg.strip()) < 20:
            return None
        v = analyse(msg, "chat")
        if v["verdict"] != "FAIL":
            return None
        lines = ["HARD %s: ...%s..." % (h["rule"], h["at"]) for h in v["hard"]]
        lines += ["%s (+%s): ...%s..." % (f["rule"], f["weight"], f["at"]) for f in v["findings"]]
        _gate.log_bounce(NAME, event, score=v["score"],
                         rules=[x["rule"] for x in v["hard"] + v["findings"]])
        return ("[slop gate] This reply reads as AI-patterned (score %s/%s). Rewrite ONLY the "
                "flagged spots in plain, varied sentences. Keep the content, the voice and the "
                "facts, then finish. Do not narrate this check in the reply.\n%s"
                % (v["score"], v["threshold"], "\n".join(lines)[:REASON_CAP]))
    except Exception:
        return None


def learn(regex, note, weight):
    re.compile(regex)    # raises on a bad pattern before anything is written
    _gate.append_private(learned_path(), json.dumps(
        {"regex": regex, "note": note, "weight": weight}) + "\n")


def main(argv):
    p = argparse.ArgumentParser(description="Score text for AI-writing patterns.")
    p.add_argument("--score", metavar="FILE", help="file to score, or - for stdin")
    p.add_argument("--profile", choices=list(THRESHOLD), default="chat")
    p.add_argument("--json", action="store_true")
    p.add_argument("--learn", metavar="REGEX")
    p.add_argument("--note", default="")
    p.add_argument("--weight", type=float, default=1.0)
    a = p.parse_args(argv)
    if a.learn:
        learn(a.learn, a.note, a.weight)
        return 0
    try:
        text = sys.stdin.read() if a.score == "-" else open(a.score, encoding="utf-8", errors="replace").read()
    except Exception as exc:
        print("slop gate: could not read input: %s" % exc, file=sys.stderr)
        return 2
    r = analyse(text, a.profile)
    print(json.dumps(r, ensure_ascii=False) if a.json else render(r))
    return 1 if r["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main(sys.argv[1:]))
    _gate.run_stop_gate(check)
