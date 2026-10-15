#!/usr/bin/env python3
"""Image tripwire: "there are no photos of X" needs an image search behind it.

Bounces a reply that claims no images exist of something when this turn ran no image
search. Deterministic and free: a reply with no such claim costs one regex, and the
transcript is opened only when a claim matches.

What counts as an image search this turn:
  * WebSearch, or any MCP tool whose name says search, with image/photo/picture in the query
  * any tool call whose input carries an image-search URL (Google tbm=isch, Bing Images,
    DuckDuckGo images), whether through WebFetch, curl or a browser tool
  * a Bash command matching CLAUDE_GATE_IMAGE_SEARCH_RX, for your own search script
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402

NAME = "image"
SUBJECT_CAP = 60

CLAIM_RXS = (
    re.compile(r"\bno\s+(?:images|photos|pictures|pics|shots)\s+(?:of|for)\s+(?P<x>[^.,;!?\n]{2,60})", re.I),
    re.compile(r"\bno\s+(?:images|photos|pictures|pics|shots)\s+(?:exist|existed)\b", re.I),
    re.compile(r"\b(?:could\s?n[o']?t|can\s?n[o']?t|cannot|unable\s+to)\s+find\s+(?:any\s+)?"
               r"(?:images|photos|pictures|pics|shots)(?:\s+(?:of|for)\s+(?P<x>[^.,;!?\n]{2,60}))?", re.I),
    re.compile(r"\bthere\s+(?:are|were|is|was)\s+no\s+(?:images|photos|pictures|pics|shots)"
               r"(?:\s+(?:of|for)\s+(?P<x>[^.,;!?\n]{2,60}))?", re.I),
    re.compile(r"\bnothing\s+(?:came|turned)\s+up\s+for\s+(?P<x>[^\n]{0,70}?(?:image|photo|picture)\w*)", re.I),
    re.compile(r"\bno\s+interior\s+(?:shots|photos|pictures|images)\b", re.I),
)
IMAGE_URL_RX = re.compile(r"tbm=isch|udm=2\b|images\.google|bing\.com/images|duckduckgo\.com/.*iax=images", re.I)
IMAGE_QUERY_RX = re.compile(r"image|photo|picture", re.I)
SEARCH_TOOL_RX = re.compile(r"^WebSearch$|^mcp__.*search", re.I)
SUBJECT_TAIL_RX = re.compile(
    r"\s+(?:anywhere\s+)?(?:online|on\s+the\s+(?:web|internet)|on\s+google|at\s+all"
    r"|available|out\s+there|that\s+I\s+(?:could|can)\s+find)\s*$", re.I)


def image_claim(msg):
    """The subject of a no-images claim, or None. Code is stripped first so the gate never
    fires on its own documentation, and quoting the bounce is not making the claim."""
    try:
        body = re.sub(r"```.*?```", " ", str(msg or ""), flags=re.S)
        body = re.sub(r"`[^`\n]*`", " ", body)
        if "[image tripwire]" in body:
            return None
        for rx in CLAIM_RXS:
            m = rx.search(body)
            if not m:
                continue
            x = (m.groupdict().get("x") or "").strip(" \t'\"*_-“”‘’")
            x = re.sub(r"^(?:the|a|an|any|that|this|it)\s+", "", x, flags=re.I).strip()
            x = SUBJECT_TAIL_RX.sub("", x).strip(" \t,")
            return (x or "that")[:SUBJECT_CAP]
        return None
    except Exception:
        return None


def search_ran(turn):
    """Did THIS turn run an image search? True when the transcript is unreadable: a gate
    that cannot see the evidence must never block an honest reply."""
    try:
        uses = turn.tool_uses()
        if uses is None:
            return True
        extra = os.environ.get("CLAUDE_GATE_IMAGE_SEARCH_RX")
        extra_rx = re.compile(extra) if extra else None
        for name, inp in uses:
            if SEARCH_TOOL_RX.search(name) and IMAGE_QUERY_RX.search(str(inp.get("query") or "")):
                return True
            # str(inp), not inp["url"]: a browser batch carries its URL inside a nested list
            if IMAGE_URL_RX.search(json.dumps(inp)):
                return True
            if extra_rx and name == "Bash" and extra_rx.search(str(inp.get("command") or "")):
                return True
        return False
    except Exception:
        return True


def check(event, turn=None):
    """Stop gate. -> block reason, or None. Never raises."""
    try:
        if event.get("stop_hook_active") or _gate.gate_off(NAME):
            return None
        subject = image_claim(_gate.reply_of(event))
        if not subject:
            return None
        if search_ran(turn or _gate.Turn(event)):
            return None
        _gate.log_bounce(NAME, event, subject=subject)
        return ("[image tripwire] This reply says there are no images of %s and no image search "
                "ran this turn. Run one first (a web search for images of \"%s\", or an image-search "
                "URL through a fetch or browser tool), then attach what you found or say exactly "
                "which engines returned nothing." % (subject, subject))
    except Exception:
        return None


if __name__ == "__main__":
    _gate.run_stop_gate(check)
