#!/usr/bin/env python3
"""Runtime-truth tripwire: a claim about how the tool itself works needs a read this turn.

Models describe their own harness from memory with full confidence: "hooks run in
order", "that setting needs a restart", "/compact keeps your files". Training data on a
fast-moving CLI is stale by definition. This bounces a reply that asserts how Claude Code,
its hooks, settings, models or plugins behave when the turn read no doc, settings file,
`--help` output or source that governs it.

Same cost order as the cutoff tripwire: a free topic gate, a free evidence check, then
the shared judge call. Extra paths that count as a governing read (a local docs mirror,
your own tooling): CLAUDE_GATE_RUNTIME_DOCS=path1:path2
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402

NAME = "runtime"
QUOTE_CAP = 300
HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_UNSET = object()

# TOPIC gate, stated honestly: a vocabulary list, not the detector. A mechanism claim
# written without any of these words escapes and is never judged. The gate exists so a
# reply about groceries costs zero model calls, not to be complete. Word-boundary, so
# "hook" does not fire inside "hookah".
DOMAIN_RX = re.compile(
    r"(?i)(?:/model\b|/compact\b|\bclaude code\b|\bhot-reload\w*|\bhooks?\b|\bconfig\w*"
    r"|\brestart\w*|\breload\w*|\bcompaction\b|\bsettings?\b|\bsubscriptions?\b|\bbilling\b"
    r"|\btokens?\b|\bsessions?\b|\bsandbox\w*|\bmcp\b|\bplugins?\b|\bpermissions?\b"
    r"|\bsubagents?\b|\bslash commands?\b|\bclaude\.md\b|\bheadless\b|\bskills?\b)")

SOURCE_RX = re.compile(r"""(?xi)
    \.claude[^/\s'"]*/settings(?:\.local)?\.json
  | \.claude\.json
  | (?:^|[/\s'"])CLAUDE\.md\b
  | \.claude/(?:agents|commands|skills|hooks|plugins)/
  | \.mcp\.json
  | docs\.claude\.com | docs\.anthropic\.com | code\.claude\.com | claude\.com/docs
  | github\.com/anthropics/claude-code
""")
CMD_RX = re.compile(r"(?i)\bclaude\s+(?:--help|-h\b|--version|doctor\b|config\b|mcp\b|plugin\b)"
                    r"|\bclaude\b[^|;&\n]*\s--help\b")
NOT_EVIDENCE = {"Write", "Edit", "MultiEdit", "NotebookEdit", "TodoWrite"}

QUESTION = ("mechanism_claim", "quote_mechanism", """QUESTION -> mechanism_claim / quote_mechanism
Set mechanism_claim to true IF AND ONLY IF the reply ASSERTS AS FACT how a software system
behaves, specifically the AI coding tool the reply runs in (Claude Code), its hooks,
settings, models, configuration, permissions, plugins, MCP servers, billing or plans, or
the machine it runs on. Assertions of behavior include: what happens automatically, what
does or does not require a restart or reload, what a setting or flag does, where something
appears or is declared, what is or is not needed to make something work, what is free or
costs money.

Set mechanism_claim to false for: opinions, questions, plans and intentions ("I'll check",
"let's try"), pure descriptions of what the speaker just did, quoted text from the user,
requests for confirmation, and statements about non-software subjects.""")


def doc_fragments():
    return [HOOKS_DIR] + [os.path.expanduser(p) for p in
                          os.environ.get("CLAUDE_GATE_RUNTIME_DOCS", "").split(":") if p.strip()]


def runtime_evidence(turn):
    """-> [hit] for every read of a governing source this turn, or None when unreadable.
    A web search is NOT evidence here: it says nothing about how this install is wired."""
    uses = turn.tool_uses()
    if uses is None:
        return None
    hits, docs = [], doc_fragments()
    for name, inp in uses:
        if name in NOT_EVIDENCE:
            continue
        hay = str(inp.get("command") or "") if name == "Bash" else json.dumps(inp)
        if SOURCE_RX.search(hay):
            hits.append("source:" + SOURCE_RX.search(hay).group(0).strip())
        elif name == "Bash" and CMD_RX.search(hay):
            hits.append("cmd:" + CMD_RX.search(hay).group(0))
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
            return False
        ev = runtime_evidence(turn)
        if ev is None or ev:
            return False
        return True
    except Exception:
        return False


def reason_from_verdict(event, verdict):
    try:
        if not verdict or verdict.get("mechanism_claim") is not True:
            return None
        msg = _gate.reply_of(event)
        quote = str(verdict.get("quote_mechanism") or "").strip()[:QUOTE_CAP] or " ".join(msg.split())[:QUOTE_CAP]
        _gate.log_bounce(NAME, event, quote=quote)
        return ("[runtime-truth tripwire] This reply asserts how the tool itself works ('%s') and "
                "no docs, settings or source read happened this turn. Read the doc, config or "
                "source that governs it, then cite it or correct the sentence, then finish." % quote)
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
