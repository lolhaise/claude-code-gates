#!/usr/bin/env python3
"""Cutoff tripwire: a current fact stated with no live read behind it gets bounced.

A price, a context window, who runs a company, what a product supports: recited flat from
training data, with no web or doc read this turn. The bounce asks the one question that
matters, "where did this come from?", and offers two ways out: verify it with a live
source, or mark it in the reply as unverified training-data knowledge.

Cost-ordered so an ordinary reply spends nothing:
  1. topic gate (free regex): time-sensitive vocabulary OR a specificity marker (money,
     date, number with a unit, a year, a 4+ word quotation, a multi-word proper name).
     A marker the USER typed this turn is subtracted: repeating their number back to them
     is not a claim about the world.
  2. evidence (free): any web search, web fetch, curl, or read of a docs path this turn
     means a live source WAS consulted, and the gate stands down.
  3. judge (one `claude -p` call, shared with the runtime-truth tripwire): does a
     SPECIFIC claim come from training data rather than the conversation?

The topic gate over-fires on purpose; the judge is the detector. Extra docs paths that
count as a live read: CLAUDE_GATE_CUTOFF_DOCS=path1:path2
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402

NAME = "cutoff"
QUOTE_CAP = 300
_UNSET = object()

DOMAIN_RX = re.compile(r"""(?xi)
    \blatest\b | \bnewest\b | \bcurrent(?:ly)?\b | \bas\s+of\b | \brecent(?:ly)?\b
  | \bjust\s+(?:released|launched|announced|shipped|dropped)\b
  | \bversion\s*\d | \bv\d+\.\d
  | \bpricing\b | \bprices?\b | \bcosts?\s+\$? | \$\s?\d | \bper\s+(?:month|year|token|million)\b
  | \bdeprecated\b | \bno\s+longer\b | \bnow\s+(?:supports?|has|have|does|offers?|costs?)\b
  | \bcontext\s+window\b | \b\d+\s?k\s+tokens?\b | \btokens?\s+per\b
  | \bgpt-?\d | \bclaude\s+\d | \bopus\b | \bsonnet\b | \bhaiku\b | \bgemini\b | \bllama\b
  | \bgrok\b | \bmistral\b | \bdeepseek\b
  | \bbenchmarks?\b | \bsota\b | \bstate[-\s]of[-\s]the[-\s]art\b
  | \bceo\b | \bcto\b | \bacquired\b | \bacquisition\b | \bmerger\b | \bipo\b | \bshut\s+down\b
  | \brelease(?:d)?\s+(?:date|in|on)\b | \bshipped\s+in\b | \blaunched\s+in\b
  | \btariffs?\b | \binterest\s+rates?\b | \bregulations?\b | \bthe\s+law\b
  | \bstock\s+price\b | \bmarket\s+cap\b | \bshare\s+price\b
""")

SPEC_MONEY_RX = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s?[kKmMbB]?\b")
SPEC_DATE_RX = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?\b")
SPEC_NUM_UNIT_RX = re.compile(
    r"(?i)\b\d[\d,]*(?:\.\d+)?\s?[kmb]\b"
    r"|\b\d[\d,]*(?:\.\d+)?\s*(?:%|percent|people|users|seats|employees|customers|"
    r"km|miles|hours|days|weeks|months|years|tokens|parameters|params)\b")
SPEC_YEAR_RX = re.compile(r"\b(?:19|20)\d{2}\b")
SPEC_QUOTE_RX = re.compile("[\"“]([^\"”\n]{8,300})[\"”]")
# Multi-word only: a weekday and every sentence-initial word are capitalised, none is a claim.
SPEC_PROPER_RX = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+(?:of\s+|de\s+|the\s+)?[A-Z][a-z]{2,})+\b")

WORLD_TOOL_RX = re.compile(r"^(?:WebSearch|WebFetch)$|^mcp__.*(?:search|fetch|brows|navigate|scrape|crawl)", re.I)
WORLD_CMD_RX = re.compile(r"(?i)\bcurl\b|\bwget\b|\bgh\s+api\b|\blynx\s+-dump\b|\bw3m\s+-dump\b")
NOT_EVIDENCE = {"Write", "Edit", "MultiEdit", "NotebookEdit", "TodoWrite"}

QUESTION = ("stale_world_claim", "quote_stale", """QUESTION -> stale_world_claim / quote_stale
Decide this by PROVENANCE, where the sentence could have come from, not by how confident
it sounds. A claim stated with total confidence and no source is the case this question
exists for.

For each specific factual statement in the reply, ask which bucket it came from:
  (1) a tool result in this conversation (a search, a fetch, a command's output)
  (2) the speaker's own notes, files or workspace, which the reply names or quotes
  (3) something the user said in this conversation
  (4) the model's training data, i.e. nothing in this conversation supports it

Set stale_world_claim to true IF AND ONLY IF the reply makes a SPECIFIC VERIFIABLE claim
(a number, a named person/company/product, a date, a price, a version, a quotation) about
something OUTSIDE the speaker's own files and this conversation, stated as fact, with no
source named in the reply and no hedge such as "as of my last read", "unverified",
"I think", "I believe". That is bucket 4, and only bucket 4 with a specific claim is true.

Set stale_world_claim to false for: buckets 1, 2 and 3; facts about the user's own life,
files, machine or workspace; quoted text from the user; opinions, jokes and predictions;
plans and intentions; vague statements carrying no checkable specific; and claims about
how the assistant's own tooling behaves (a separate question covers those).""")


def specificity_hits(msg, user_text=""):
    """-> [marker] the reply carries that a training-set recital would carry."""
    try:
        t = re.sub(r"```.*?```|`[^`\n]*`|https?://\S+", " ", str(msg or ""), flags=re.S)
        u = str(user_text or "").lower()
        hits = []
        for label, rx in (("money", SPEC_MONEY_RX), ("date", SPEC_DATE_RX),
                          ("number+unit", SPEC_NUM_UNIT_RX), ("year", SPEC_YEAR_RX)):
            for m in rx.finditer(t):
                if m.group(0).strip().lower() in u:
                    continue                       # bucket 3: the user typed it
                hits.append(label)
                break
        for m in SPEC_QUOTE_RX.finditer(t):
            g = m.group(1)
            if len(g.split()) >= 4 and g.strip().lower() not in u:
                hits.append("quotation")
                break
        for m in SPEC_PROPER_RX.finditer(t):
            if m.group(0).lower() not in u:
                hits.append("proper-noun:" + m.group(0)[:40])
                break
        return hits
    except Exception:
        return []


def doc_fragments():
    return [os.path.expanduser(p) for p in os.environ.get("CLAUDE_GATE_CUTOFF_DOCS", "").split(":") if p.strip()]


def world_evidence(turn):
    """-> [hit] for every tool call this turn that read the outside world, or None when the
    transcript is unreadable (the caller then fails open)."""
    uses = turn.tool_uses()
    if uses is None:
        return None
    hits, docs = [], doc_fragments()
    for name, inp in uses:
        if name in NOT_EVIDENCE:
            continue
        if WORLD_TOOL_RX.search(name):
            hits.append("tool:" + name)
            continue
        hay = str(inp.get("command") or "") if name == "Bash" else json.dumps(inp)
        if name == "Bash" and WORLD_CMD_RX.search(hay):
            hits.append("cmd:" + WORLD_CMD_RX.search(hay).group(0))
        elif any(d and d in hay for d in docs):
            hits.append("doc:" + name)
    return hits


def gate_open(event, turn):
    """The FREE half: is a judge call warranted at all?"""
    try:
        if event.get("stop_hook_active") or _gate.gate_off(NAME):
            return False
        msg = _gate.reply_of(event)
        if len(msg.strip()) < _gate.MIN_REPLY:
            return False
        if not DOMAIN_RX.search(_gate.strip_code(msg)):
            if not specificity_hits(msg, turn.user_text()):
                return False
        ev = world_evidence(turn)
        if ev is None or ev:
            return False                 # unreadable (fail open) or a live source WAS read
        return True
    except Exception:
        return False


def reason_from_verdict(event, verdict):
    try:
        if not verdict or verdict.get("stale_world_claim") is not True:
            return None                  # false, or could not judge: allow
        msg = _gate.reply_of(event)
        quote = str(verdict.get("quote_stale") or "").strip()[:QUOTE_CAP] or " ".join(msg.split())[:QUOTE_CAP]
        _gate.log_bounce(NAME, event, quote=quote)
        return ("[cutoff tripwire] This reply states '%s' as current fact and no web search, web "
                "fetch or docs read happened this turn. Where did this come from? If it is from "
                "training data, say so in the reply or verify it. Verify it with a live source and "
                "cite it, or mark it explicitly as unverified training-data knowledge, then finish."
                % quote)
    except Exception:
        return None


def check(event, turn=None, verdict=_UNSET):
    """Stop gate. -> block reason, or None. Never raises."""
    try:
        turn = turn or _gate.Turn(event)
        if not gate_open(event, turn):
            return None
        if verdict is _UNSET:
            verdict = _gate.judge([QUESTION], _gate.reply_of(event))
        return reason_from_verdict(event, verdict)
    except Exception:
        return None


if __name__ == "__main__":
    _gate.run_stop_gate(check)
